"""La logica del helper del cuadro de guardado, sin COM y sin ventanas.

`uia_helper` recibe su cliente de UI Automation como PARAMETRO en cada paso, y
eso es lo que permite probar aqui lo que de verdad decide: cuando se da por
comprometida la eleccion de tipo, cuando se declara que el nombre no se
escribio, y las verificaciones que tienen que fallar ANTES de un clic real.

Lo unico que no se prueba aqui es COM hablando con Windows. Eso lo ejercitan
las pruebas `live`, que CI no puede correr porque no hay Power BI Desktop en un
runner. Todo lo demas -que es donde estaban los defectos- si.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

from horizun_pbi_mcp.powerbi import uia_helper


# ------------------------------------------------------------- los dobles ---
class _Elemento:
    def __init__(self, nombre="", automation_id=""):
        self.CurrentName = nombre
        self.CurrentAutomationId = automation_id


class _UiaFalso:
    """Un cuadro de guardado de mentira, con memoria de lo que le pidieron."""

    def __init__(self, *, opciones=None, valor_tipo=None, estado_tras=0,
                 valor_nombre=None, hay_boton=True, hay_tipo=True,
                 hay_nombre=True, invocar=None,
                 id_del_boton=uia_helper.AUTOMATION_ID_GUARDAR):
        self.opciones = [_Elemento(o) for o in (opciones or [
            "Archivo de Power BI (*.pbix)",
            "Archivos de plantilla de Power BI (*.pbit)",
            "Archivos de proyecto Power BI (*.pbip)"])]
        self.valor_tipo = valor_tipo
        self.estado_tras = estado_tras
        self.valor_nombre = valor_nombre
        self.hay_boton = hay_boton
        self.hay_tipo = hay_tipo
        self.hay_nombre = hay_nombre
        self._invocar = invocar
        self.id_del_boton = id_del_boton
        self.invocados = []
        self.expandidos = 0
        self.enfocados = 0

    def desde_hwnd(self, hwnd):
        return f"elemento-{hwnd}"

    def por_id(self, raiz, automation_id, tipo):
        if automation_id == uia_helper.AUTOMATION_ID_TIPO:
            return "combo-tipo" if self.hay_tipo else None
        if automation_id == uia_helper.AUTOMATION_ID_NOMBRE:
            return "combo-nombre" if self.hay_nombre else None
        if automation_id == uia_helper.AUTOMATION_ID_GUARDAR:
            if not self.hay_boton:
                return None
            return _Elemento("Guardar", self.id_del_boton)
        return None

    def valor(self, elemento):
        return self.valor_tipo if elemento == "combo-tipo" else self.valor_nombre

    def expandir(self, combo):
        self.expandidos += 1

    def items(self, combo):
        return self.opciones

    def invocar(self, elemento):
        self.invocados.append(elemento)
        if self._invocar is not None:
            return self._invocar(elemento)
        return "invoke"

    def estado_expandido(self, combo):
        return self.estado_tras

    def enfocar(self, elemento):
        self.enfocados += 1
        return True

    def punto_clicable(self, elemento):
        return (100, 200)


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)


# ================ 1) el tipo: elegido Y procesado, no solo pintado ==========
def test_el_tipo_se_da_por_bueno_cuando_la_lista_se_cierra():
    """Que la lista se cierre es la señal de que la aplicacion lo proceso."""
    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)",
                    estado_tras=uia_helper.ESTADO_CERRADO)

    paso = uia_helper._elegir_tipo(uia, 22, ".pbix")      # noqa: SLF001

    assert paso["file_type_selected"] == "Archivo de Power BI (*.pbix)"
    assert paso["via"] == "invoke"
    assert paso["expand_state_after"] == uia_helper.ESTADO_CERRADO
    assert len(uia.opciones) == len(paso["available"])


def test_si_la_lista_sigue_abierta_NO_se_da_por_comprometido():
    """`Select()` repinta y deja la lista abierta: eso no es haber elegido.

    Es el defecto original visto desde el otro lado: el desplegable decia
    `.pbix` y Desktop seguia guardando un proyecto.
    """
    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)", estado_tras=1)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(uia, 22, ".pbix")        # noqa: SLF001

    assert "sigue abierto" in str(fallo.value)
    assert fallo.value.detalles["expand_collapse_state"] == 1


def test_si_el_combo_acaba_en_otra_cosa_se_dice():
    uia = _UiaFalso(valor_tipo="Archivos de proyecto Power BI (*.pbip)",
                    estado_tras=uia_helper.ESTADO_CERRADO)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(uia, 22, ".pbix")        # noqa: SLF001
    assert "no quedo en lo pedido" in str(fallo.value)


def test_si_no_ofrecen_pbix_se_dice_que_ofrecen(sin_esperas):
    uia = _UiaFalso(opciones=["Archivos de plantilla de Power BI (*.pbit)"])

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(uia, 22, ".pbix")        # noqa: SLF001
    assert fallo.value.detalles["available"] == [
        "Archivos de plantilla de Power BI (*.pbit)"]


def test_sin_desplegable_de_tipo_no_se_sigue():
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(_UiaFalso(hay_tipo=False), 22,  # noqa: SLF001
                                ".pbix")
    assert fallo.value.detalles["automation_id"] == uia_helper.AUTOMATION_ID_TIPO


# ============ 2) el nombre: se teclea y se RELEE, no se supone =============
def test_la_ruta_se_relee_del_campo_antes_de_seguir(monkeypatch):
    escrito = {}
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: escrito.__setitem__("texto", t))
    ruta = "C:\\entrega\\Informe.pbix"
    uia = _UiaFalso(valor_nombre=ruta)

    paso = uia_helper._escribir_ruta(uia, 22, ruta)      # noqa: SLF001

    assert escrito["texto"] == ruta
    assert paso["filename_verified"] is True
    assert uia.enfocados == 1


def test_si_el_campo_no_quedo_con_la_ruta_se_para(monkeypatch):
    """Fue el sintoma del `INPUT` recreado: no se tecleaba nada."""
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: None)
    monkeypatch.setattr(uia_helper, "ESPERA_INTERFAZ", 0.2)
    uia = _UiaFalso(valor_nombre="")

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(uia, 22, "C:\\x\\a.pbix")  # noqa: SLF001

    assert "no quedo con la ruta pedida" in str(fallo.value)
    # La ruta NO viaja en el error: solo su longitud.
    assert "C:\\x\\a.pbix" not in str(fallo.value.detalles)
    assert fallo.value.detalles["expected_len"] == len("C:\\x\\a.pbix")


# =========== 3) confirmar: se prueba lo barato antes que el clic ===========
def test_si_invoke_cierra_el_cuadro_no_se_hace_ningun_clic(monkeypatch):
    """El primer metodo basta contra Desktop real; el clic es el ultimo recurso."""
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: False)
    clics = []
    monkeypatch.setattr(uia_helper, "clic_dinamico",
                        lambda *a, **k: clics.append(a))
    uia = _UiaFalso()

    paso = uia_helper._confirmar(uia, 22, 4321)          # noqa: SLF001

    assert paso["commit_method"] == "invoke"
    assert clics == [], "se hizo un clic real habiendo funcionado el primero"


def test_sin_boton_de_guardar_no_se_inventa_donde_pulsar():
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._confirmar(_UiaFalso(hay_boton=False), 22, 1)  # noqa: SLF001
    assert fallo.value.detalles["automation_id"] == uia_helper.AUTOMATION_ID_GUARDAR


def test_si_no_se_puede_poner_al_frente_no_se_pulsa_a_ciegas(monkeypatch):
    """Un clic sin el cuadro al frente cae en la aplicacion de otra persona."""
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: False)
    clics = []
    monkeypatch.setattr(uia_helper, "clic_dinamico",
                        lambda *a, **k: clics.append(a))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._confirmar(_UiaFalso(), 22, 4321)     # noqa: SLF001

    assert "frente" in str(fallo.value).casefold()
    assert clics == []


# ========== 4) el clic real: se aborta si algo no cuadra, siempre ==========
class _Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


def test_un_punto_fuera_del_cuadro_no_se_pulsa(monkeypatch):
    monkeypatch.setattr(uia_helper, "_rect_ventana",
                        lambda h: _Rect(0, 0, 50, 50))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.clic_dinamico((500, 500), 22, 4321)

    assert "fuera del cuadro" in str(fallo.value)
    assert fallo.value.detalles["point"] == [500, 500]


def test_una_ventana_de_otro_proceso_bajo_el_punto_aborta(monkeypatch):
    """El punto puede caer bien y haber otra ventana encima."""
    import ctypes

    monkeypatch.setattr(uia_helper, "_rect_ventana",
                        lambda h: _Rect(0, 0, 500, 500))

    class _Fn:
        def __init__(self, valor=0, efecto=None):
            self.valor, self.efecto = valor, efecto
            self.argtypes = self.restype = None

        def __call__(self, *a):
            return self.efecto(*a) if self.efecto else self.valor

    def _duenio(hwnd, puntero):
        puntero._obj.value = 9999          # otro proceso
        return 1

    class _U32:
        def __init__(self):
            self.WindowFromPoint = _Fn(1234)
            self.GetWindowThreadProcessId = _Fn(efecto=_duenio)

        def __getattr__(self, n):
            return _Fn()

    monkeypatch.setattr(uia_helper, "_user32", lambda: _U32())
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.clic_dinamico((100, 100), 22, 4321)

    assert fallo.value.detalles["owner_pid"] == 9999
    assert fallo.value.detalles["expected_pid"] == 4321


def test_el_clic_es_UNO_solo_y_en_coordenadas_calculadas(monkeypatch):
    """Un solo abajo/arriba, y el punto convertido a coordenadas absolutas.

    Nada de esto sale de una constante: el punto viene del elemento en el
    momento, y la conversion depende del tamano real de la pantalla.
    """
    import ctypes

    monkeypatch.setattr(uia_helper, "_rect_ventana",
                        lambda h: _Rect(0, 0, 1920, 1080))

    class _Fn2:
        def __init__(self, valor=0, efecto=None):
            self.valor, self.efecto = valor, efecto
            self.argtypes = self.restype = None

        def __call__(self, *a):
            return self.efecto(*a) if self.efecto else self.valor

    def _duenio(hwnd, puntero):
        puntero._obj.value = 4321
        return 1

    class _U:
        def __init__(self):
            self.WindowFromPoint = _Fn2(1234)
            self.GetWindowThreadProcessId = _Fn2(efecto=_duenio)
            self.GetSystemMetrics = _Fn2(efecto=lambda i: 1921 if i == 0 else 1081)

        def __getattr__(self, n):
            return _Fn2()

    monkeypatch.setattr(uia_helper, "_user32", lambda: _U())
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    tandas = []
    monkeypatch.setattr(uia_helper, "_enviar_teclas",
                        lambda eventos: tandas.append(eventos))

    detalle = uia_helper.clic_dinamico((960, 540), 22, 4321)

    assert detalle["point"] == [960, 540]
    assert detalle["owner_pid"] == 4321
    # mover, pulsar, soltar: exactamente un clic, no dos.
    assert len(tandas) == 3 and all(len(t) == 1 for t in tandas)


# ================= 5) la identidad del proceso, antes de nada =============
def test_un_pid_que_ya_no_es_desktop_se_rechaza(monkeypatch):
    import psutil

    class _Proc:
        def __init__(self, pid):
            pass

        def name(self):
            return "notepad.exe"

        def create_time(self):
            return 1000.0

    monkeypatch.setattr(psutil, "Process", _Proc)
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.verificar_proceso(4321, None)
    assert fallo.value.detalles["actual_process"] == "notepad.exe"


def test_un_pid_reciclado_se_caza_por_la_hora_de_arranque(monkeypatch):
    """Windows reutiliza PIDs; el numero solo no identifica un proceso."""
    import psutil

    class _Proc:
        def __init__(self, pid):
            pass

        def name(self):
            return "PBIDesktop.exe"

        def create_time(self):
            return 5000.0

    monkeypatch.setattr(psutil, "Process", _Proc)
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.verificar_proceso(4321, 1000.0)
    assert fallo.value.detalles["expected_started"] == 1000.0
    assert fallo.value.detalles["actual_started"] == 5000.0


def test_un_proceso_que_ya_no_existe_se_dice_asi(monkeypatch):
    import psutil

    def _revienta(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", _revienta)
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.verificar_proceso(4321, 1000.0)
    assert fallo.value.detalles["cause"] == "NoSuchProcess"


# ============ 6) el protocolo por stdin/stdout: UNA linea de JSON ==========
def _correr_main(monkeypatch, entrada: str):
    salida = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(entrada))
    monkeypatch.setattr(sys, "stdout", salida)
    codigo = uia_helper.main()
    return codigo, salida.getvalue()


def test_una_peticion_ilegible_se_contesta_en_json(monkeypatch):
    """El padre parsea stdout entero: no puede recibir una traza suelta."""
    codigo, salida = _correr_main(monkeypatch, "esto no es json")

    assert codigo == 2
    datos = json.loads(salida)
    assert datos["ok"] is False and datos["phase"] == "peticion"


def test_una_accion_desconocida_dice_cuales_valen(monkeypatch):
    codigo, salida = _correr_main(monkeypatch, json.dumps({"action": "volar"}))

    assert codigo == 2
    datos = json.loads(salida)
    assert datos["valid"] == ["fit_to_page", "save_as", "select_page"]
    assert "volar" in datos["error"]


def test_un_fallo_del_paso_sale_con_su_fase_y_sus_detalles(monkeypatch):
    def _revienta(peticion):
        raise uia_helper.HelperError("tipo", "no ofrece pbix", available=["a"])

    monkeypatch.setitem(uia_helper.ACCIONES, "save_as", _revienta)
    codigo, salida = _correr_main(monkeypatch, json.dumps({"action": "save_as"}))

    assert codigo == 1
    datos = json.loads(salida)
    assert datos["phase"] == "tipo"
    assert datos["details"]["available"] == ["a"]


def test_un_fallo_inesperado_no_deja_al_padre_sin_respuesta(monkeypatch):
    def _revienta(peticion):
        raise RuntimeError("algo raro")

    monkeypatch.setitem(uia_helper.ACCIONES, "save_as", _revienta)
    codigo, salida = _correr_main(monkeypatch, json.dumps({"action": "save_as"}))

    assert codigo == 1
    datos = json.loads(salida)
    assert datos["phase"] == "inesperado"
    assert "RuntimeError" in datos["error"]


def test_lo_que_sale_va_redactado(monkeypatch):
    """El padre lo propaga a un cliente MCP: no puede identificar a quien corre.

    Se usa la MISMA regla que el resto del repo (`services.redaction.rutas`),
    que sustituye el directorio personal por `~`. Antes habia una copia de esa
    regla escrita dentro del helper, y dos definiciones de "ruta personal"
    acaban divergiendo: la que diverge es siempre la que nadie mira.
    """
    import os

    casa = os.path.expanduser("~")
    ruta = os.path.join(casa, "Secreto", "x.pbix")

    def _revienta(peticion):
        raise uia_helper.HelperError("nombre", f"fallo en {ruta}")

    monkeypatch.setitem(uia_helper.ACCIONES, "save_as", _revienta)
    _codigo, salida = _correr_main(monkeypatch, json.dumps({"action": "save_as"}))

    assert casa not in salida
    assert "~" in json.loads(salida)["error"]


def test_el_helper_no_reimplementa_la_redaccion(monkeypatch):
    """Si alguien vuelve a copiar la regla aqui dentro, esta prueba lo dice."""
    from horizun_pbi_mcp.services import redaction

    llamadas = []
    monkeypatch.setattr(redaction, "rutas",
                        lambda v: llamadas.append(v) or "REDACTADO")

    assert uia_helper._redactar("cualquier cosa") == "REDACTADO"  # noqa: SLF001
    assert llamadas == ["cualquier cosa"]

# =========== 11) sincronizacion por evento, no margenes fijos =============
def test_hasta_que_sale_en_cuanto_se_cumple_y_no_agota_el_plazo():
    """Si esperase el plazo entero seria un `sleep` con mas pasos."""
    import time as reloj

    intentos = {"n": 0}

    def _condicion():
        intentos["n"] += 1
        return "listo" if intentos["n"] >= 3 else None

    t0 = reloj.monotonic()
    assert uia_helper._hasta_que(_condicion, plazo=30,       # noqa: SLF001
                                 cada=0.01) == "listo"
    assert reloj.monotonic() - t0 < 5, "espero mucho mas de lo necesario"
    assert intentos["n"] == 3


def test_hasta_que_agotado_devuelve_None_sin_fingir():
    assert uia_helper._hasta_que(lambda: None, plazo=0.15,   # noqa: SLF001
                                 cada=0.01) is None


def test_el_nombre_se_acepta_aunque_la_aplicacion_vaya_con_retraso(monkeypatch):
    """El fallo real: la maquina ocupada consume las teclas mas tarde.

    Con un margen fijo de 0.4 s el campo se leia a medias -76 caracteres
    pedidos, 31 leidos- y se reportaba como fallo de escritura. Lo que fallaba
    era la sincronizacion, no el tecleo.
    """
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: None)
    ruta = r"C:\entrega\Informe.pbix"

    class _CampoLento(_UiaFalso):
        """Devuelve la ruta a medias las primeras veces, como Desktop bajo carga."""

        def __init__(self):
            super().__init__()
            self.lecturas = 0

        def valor(self, elemento):
            self.lecturas += 1
            if self.lecturas < 4:
                return ruta[: self.lecturas * 5]
            return ruta

    lento = _CampoLento()
    paso = uia_helper._escribir_ruta(lento, 22, ruta)        # noqa: SLF001

    assert paso["filename_verified"] is True
    assert lento.lecturas >= 4, "no llego a reintentar la lectura"


def test_si_el_campo_nunca_se_llena_se_dice_cuanto_se_espero(monkeypatch):
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: None)
    monkeypatch.setattr(uia_helper, "ESPERA_INTERFAZ", 0.2)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(                        # noqa: SLF001
            _UiaFalso(valor_nombre=r"C:\a"), 22, r"C:\entrega\Informe.pbix")

    assert fallo.value.detalles["waited"] == 0.2
    assert fallo.value.detalles["actual_len"] == len(r"C:\a")


def test_el_desplegable_se_espera_a_que_tenga_opciones(monkeypatch):
    """Leerlo justo tras expandir devolvia lista vacia y "no ofrece .pbix"."""

    class _ListaLenta(_UiaFalso):
        def __init__(self):
            super().__init__(valor_tipo="Archivo de Power BI (*.pbix)",
                             estado_tras=uia_helper.ESTADO_CERRADO)
            self.consultas = 0
            self._todas = list(self.opciones)

        def items(self, combo):
            self.consultas += 1
            return [] if self.consultas < 3 else self._todas

    lenta = _ListaLenta()
    paso = uia_helper._elegir_tipo(lenta, 22, ".pbix")       # noqa: SLF001

    assert paso["file_type_selected"] == "Archivo de Power BI (*.pbix)"
    assert lenta.consultas >= 3


def test_si_el_desplegable_nunca_ofrece_nada_se_dice(monkeypatch):
    monkeypatch.setattr(uia_helper, "ESPERA_INTERFAZ", 0.2)

    class _Vacia(_UiaFalso):
        def items(self, combo):
            return []

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(_Vacia(), 22, ".pbix")       # noqa: SLF001

    assert fallo.value.detalles["available"] == []
