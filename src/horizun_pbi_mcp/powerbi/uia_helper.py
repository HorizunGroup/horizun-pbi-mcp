"""Conduce el cuadro "Guardar como" en un PROCESO APARTE.

    python -m horizun_pbi_mcp.powerbi.uia_helper      # peticion JSON por stdin

Por que un proceso y no un hilo
-------------------------------
Una llamada COM bloqueada **no se puede cancelar**. `thread.join(timeout)`
devuelve el control al que espera, pero el hilo sigue dentro de COM para
siempre: el servidor MCP se queda con un hilo colgado por cada intento. Un
proceso si se puede terminar, y con el se va todo lo que tenga tomado.

Ademas resuelve un problema real que aparecio en la practica: `comtypes`
inicializa COM al importarse, y si pythonnet o pytest ya fijaron el
apartamento del hilo, el import falla con «Cannot change thread mode after it
is set». En un proceso nuevo no hay apartamento previo que respetar.

El contrato con el proceso padre
--------------------------------
- **stdin**: un objeto JSON con la peticion.
- **stdout**: EXACTAMENTE un objeto JSON con la respuesta. Nada mas. Ni logs,
  ni avisos, ni trazas: el padre lo parsea entero.
- **stderr**: las trazas, si hacen falta. El padre las captura y las redacta.
- El padre impone el plazo y, si se agota, termina este proceso.

Lo que se comprueba antes de tocar nada
---------------------------------------
Identidad del proceso (nombre y hora de arranque), pertenencia de la ventana a
ese PID, y que el cuadro de guardado sea el suyo. Sin eso no se pulsa nada:
teclear en la ventana de otra persona es el peor fallo posible aqui.

Lo que se aprendio de cuarenta guardados seguidos
-------------------------------------------------
Con varias ventanas de Desktop abiertas, el mismo guardado fallaba una de
cada cinco veces y funcionaba al repetirlo: el campo del nombre se quedaba a
medias (`expected_len=182, actual_len=30`), el foco se lo llevaba otra
ventana a mitad del tecleo, o el desplegable de tipo se leia antes de que se
poblara y salia `available=[]`. Ninguno de esos es un fallo de Power BI: son
carreras entre este proceso y la interfaz. Por eso ahora:

- la ruta se pone primero con `ValuePattern.SetValue`, que no depende del foco
  ni de la cola de teclado, y el tecleo queda como respaldo;
- cada fase transitoria se intenta hasta TRES veces, localizando los
  controles de nuevo en cada intento -una referencia UIA caducada no se
  reutiliza- y con un tope de tiempo por fase;
- "la lista aun no cargo" y "el formato no se ofrece" son dos errores
  distintos con dos razones distintas;
- una confirmacion de resultado incierto NO se repite: antes de volver a
  pulsar Guardar se comprueba si el cuadro ya se cerro o el archivo ya
  aparecio;
- ante fallo definitivo se intenta cancelar SOLO el cuadro de esta operacion,
  y se reporta si se consiguio.
"""
from __future__ import annotations

import functools
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# --------------------------------------------------------------- constantes --
CLASE_DIALOGO = "#32770"
AUTOMATION_ID_NOMBRE = "FileNameControlHost"
AUTOMATION_ID_TIPO = "FileTypeControlHost"
AUTOMATION_ID_GUARDAR = "1"
#: IDCANCEL. Solo se usa para LIMPIAR el cuadro de esta operacion.
AUTOMATION_ID_CANCELAR = "2"

UIA_PROP_NAME = 30005
UIA_PROP_CONTROLTYPE = 30003
UIA_PROP_AUTOID = 30011
UIA_TIPO_BUTTON = 50000
UIA_TIPO_CHECKBOX = 50002
UIA_TIPO_COMBOBOX = 50003
UIA_TIPO_LISTITEM = 50007
UIA_TIPO_MENUITEM = 50011
UIA_TIPO_RADIOBUTTON = 50013
UIA_TIPO_SPLITBUTTON = 50031
UIA_TIPO_TABITEM = 50019
UIA_TIPO_TEXT = 50020
#: IDYES del cuadro de confirmacion de reemplazo del dialogo comun.
AUTOMATION_ID_SI = "6"
#: GetWindow(GW_OWNER) y GetAncestor(GA_ROOT).
GW_OWNER = 4
GA_ROOT = 2

#: HRESULT de UI Automation/COM que significan "la interfaz cambio debajo":
#: elemento ya no disponible, servidor ocupado o llamada RPC caida. Solo esos
#: se reintentan; cualquier otro error COM es definitivo y un error de
#: programacion no se disfraza de transitorio.
HRESULT_TRANSITORIOS = frozenset({
    0x80040201,   # UIA_E_ELEMENTNOTAVAILABLE
    0x80131505,   # UIA_E_TIMEOUT
    0x8001010A,   # RPC_E_SERVERCALL_RETRYLATER
    0x800706BA,   # RPC_S_SERVER_UNAVAILABLE
    0x800706BE,   # RPC_S_CALL_FAILED
})
NOMBRE_BOTON_SI = re.compile(r"^(s[ií]|yes)$", re.I)
UIA_PAT_INVOCAR = 10000
UIA_PAT_VALOR = 10002
UIA_PAT_EXPANDIR = 10005
UIA_PAT_SELITEM = 10010
UIA_PAT_TOGGLE = 10015
UIA_PAT_LEGACY = 10018
UIA_SCOPE_DESC = 4
#: ExpandCollapseState: 0 = cerrado. Cerrado tras activar = hubo compromiso.
ESTADO_CERRADO = 0

#: Cuanto se espera A QUE LA INTERFAZ ALCANCE un estado concreto. No es un
#: margen: se comprueba el estado real cada 50 ms y se sale en cuanto cuadra,
#: asi que en una maquina ociosa cuesta lo mismo que antes y en una ocupada
#: deja de fallar. Agotarlo significa que la aplicacion no llego a procesar lo
#: que se le mando, y eso es lo que dice el error.
ESPERA_INTERFAZ = 6.0

#: Intentos TOTALES por fase transitoria: el primero cuenta. Tres es lo que
#: bastaba a mano -el mismo request funcionaba "al reintentar"- y no mas: si
#: la interfaz no responde tres veces seguidas, algo distinto esta pasando.
INTENTOS_POR_FASE = 3
#: Pausa entre intentos, corta y creciente. No es sincronizacion -eso lo hace
#: `_hasta_que`- sino aire para que la interfaz termine lo que estuviera
#: haciendo antes de volver a localizar los controles.
BACKOFF_ENTRE_INTENTOS = (0.3, 0.8)
#: Techo de tiempo por fase sumando todos los intentos, para que reintentar
#: no convierta un fallo de segundos en una espera de minutos.
PLAZO_POR_FASE = 25.0
#: Tras pulsar Guardar, cuanto se espera a que el cuadro se cierre ANTES de
#: plantearse repetir la confirmacion. Desktop lo cierra en menos de un
#: segundo; si en ocho no se cerro, o hay un modal o la pulsacion no llego.
ESPERA_CIERRE_TRAS_CONFIRMAR = 8.0

VK_CONTROL, VK_A, VK_RETURN = 0x11, 0x41, 0x0D
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000

#: Nombres de los controles de la interfaz de Power BI Desktop que se buscan
#: por texto. Dependen del idioma: se cubren español e ingles, que son los que
#: se han visto en maquinas reales. Si el idioma es otro, el error lo dice.
#: La pestaña de cinta se llama "Ver" en español y "View" en ingles: se
#: comprobo contra Power BI Desktop real, donde el arbol UIA publica "Ver".
#: "Vista" a secas NO existe -"Vista de informe" o "Vista de tabla" son
#: botones de modo, no la pestaña-, y exigirlo dejaba el camino de respaldo
#: sin encontrar nada.
NOMBRE_PESTANA_VISTA = re.compile(r"^(ver|vista|view)$", re.I)
NOMBRE_VISTA_DE_PAGINA = re.compile(r"vista de p[aá]gina|page view", re.I)
NOMBRE_AJUSTAR_A_PAGINA = re.compile(r"ajustar a la p[aá]gina|fit to page",
                                     re.I)
#: Titulos del dialogo de plantilla que Desktop abre al guardar como `.pbit`.
NOMBRE_DIALOGO_PLANTILLA = re.compile(r"plantilla|template", re.I)
NOMBRE_BOTON_ACEPTAR = re.compile(r"^(aceptar|ok)$", re.I)


class HelperError(Exception):
    """Fallo con fase y diagnostico, para que el padre sepa donde paro.

    `transitoria=True` marca un fallo que puede deberse a una carrera con la
    interfaz -control aun ausente, campo escrito a medias, foco perdido- y
    que por tanto merece otro intento. Lo demas es definitivo.
    """

    def __init__(self, fase: str, mensaje: str, *, transitoria: bool = False,
                 **detalles: Any):
        super().__init__(mensaje)
        self.fase = fase
        self.mensaje = mensaje
        self.transitoria = transitoria
        self.detalles = detalles


def _redactar(valor: Any, maximo: int = 300) -> str:
    """Quita el directorio personal, con la MISMA regla que el resto del repo.

    Habia una copia de la regla escrita aqui. Dos implementaciones de "que se
    considera una ruta personal" acaban divergiendo, y la que diverge es
    siempre la que nadie mira. Se importa la del paquete: es un modulo de texto
    plano, no toca COM y por tanto no altera el apartamento de este proceso.
    """
    from horizun_pbi_mcp.services import redaction

    return redaction.rutas(str(valor))[:maximo]


# ------------------------------------------------------------------ Win32 ----
def _user32():
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


@functools.lru_cache(maxsize=1)
def _estructuras_input():
    """Define INPUT una sola vez, de verdad: el cache no es un adorno.

    Sin `lru_cache` cada llamada creaba clases NUEVAS, y ctypes compara tipos
    por identidad de clase, no por forma. Mezclar un INPUT de una llamada con
    el array de otra da `incompatible types, INPUT instance instead of INPUT
    instance`: dos nombres iguales, dos clases distintas.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    return INPUT, MOUSEINPUT, KEYBDINPUT


def _enviar_teclas(eventos: List[Any]) -> None:
    """Inyecta los eventos y COMPRUEBA que el sistema los acepto.

    `SendInput` devuelve cuantos eventos inserto. Devuelve menos -sin lanzar
    nada- cuando otro proceso tiene bloqueada la entrada o cuando el escritorio
    esta bloqueado. Ignorar ese numero es como dar por escrito un nombre que
    nunca llego al cuadro: el fallo aparece mucho despues y en otro sitio.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    INPUT, _MI, _KI = _estructuras_input()
    user32 = _user32()
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                                 ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    arreglo = (INPUT * len(eventos))(*eventos)
    aceptados = user32.SendInput(len(eventos), arreglo, ctypes.sizeof(INPUT))
    if aceptados != len(eventos):
        codigo = ctypes.get_last_error()
        raise HelperError(
            "entrada_sintetica",
            f"Windows solo acepto {aceptados} de {len(eventos)} eventos de "
            f"teclado (error {codigo}). Suele significar que la sesion esta "
            "bloqueada o que otro proceso tiene tomada la entrada.",
            transitoria=True, reason="input_rejected")


def _tecla(vk: int, arriba: bool = False):
    INPUT, _MI, KEYBDINPUT = _estructuras_input()
    entrada = INPUT()
    entrada.type = INPUT_KEYBOARD
    entrada.ki = KEYBDINPUT(wVk=vk, wScan=0,
                            dwFlags=KEYEVENTF_KEYUP if arriba else 0,
                            time=0, dwExtraInfo=None)
    return entrada


def _caracter(letra: str, arriba: bool = False):
    INPUT, _MI, KEYBDINPUT = _estructuras_input()
    entrada = INPUT()
    entrada.type = INPUT_KEYBOARD
    banderas = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if arriba else 0)
    entrada.ki = KEYBDINPUT(wVk=0, wScan=ord(letra), dwFlags=banderas,
                            time=0, dwExtraInfo=None)
    return entrada


#: Cadencia del tecleo por INTENTO: (eventos por tanda, pausa entre tandas).
#: Dos eventos por caracter, asi que 40 eventos son 20 caracteres. La primera
#: cadencia es la rapida de siempre; las siguientes van mas despacio porque
#: el sintoma medido contra Power BI Desktop real fue perder caracteres: se
#: pidieron 133 y llegaron 26. El cuadro los consume a su ritmo y `SendInput`
#: no espera a nadie.
CADENCIAS_TECLEO = ((40, 0.01), (16, 0.05), (8, 0.12))


def escribir_texto_real(texto: str, *, tanda: int = 40,
                        pausa: float = 0.01) -> None:
    """Teclea el texto como lo hace una persona, caracter a caracter.

    Se usa `KEYEVENTF_UNICODE` en vez de codigos de tecla: asi el resultado no
    depende de la distribucion del teclado, que es justo lo que rompe un
    automatismo cuando cambia de maquina.

    `tanda` y `pausa` regulan la velocidad. Existen porque teclear tan rapido
    como permite `SendInput` pierde caracteres en el cuadro de guardado, y la
    ruta queda a medias.
    """
    eventos: List[Any] = []
    for letra in texto:
        eventos.append(_caracter(letra))
        eventos.append(_caracter(letra, arriba=True))
    for i in range(0, len(eventos), tanda):     # tandas: SendInput tiene tope
        _enviar_teclas(eventos[i:i + tanda])
        time.sleep(pausa)


def seleccionar_todo() -> None:
    _enviar_teclas([_tecla(VK_CONTROL), _tecla(VK_A)])
    time.sleep(0.05)
    _enviar_teclas([_tecla(VK_A, arriba=True), _tecla(VK_CONTROL, arriba=True)])
    time.sleep(0.1)


def _rect_ventana(hwnd: int):
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect


def _duenio_de_ventana(hwnd: int) -> int:
    """PID al que pertenece esa ventana. 0 si no existe."""
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    duenio = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(duenio))
    return int(duenio.value)


def _propietaria(hwnd: int) -> int:
    """La ventana que POSEE a esta (un MessageBox la tiene). 0 si ninguna."""
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    return int(user32.GetWindow(wintypes.HWND(hwnd), GW_OWNER) or 0)


def _pertenece_al_cuadro(hwnd: int, cuadro_hwnd: int) -> bool:
    """True si `hwnd` es el cuadro o una ventana que el cuadro posee.

    Es lo que distingue el "¿reemplazar?" que abrio NUESTRO cuadro de
    guardado de cualquier otro dialogo del mismo proceso: solo sobre el
    primero se puede pulsar algo, y solo si quien llama lo autorizo.
    """
    actual = int(hwnd)
    for _ in range(5):
        if actual == int(cuadro_hwnd):
            return True
        actual = _propietaria(actual)
        if not actual:
            return False
    return False


def _raiz_de(hwnd: int) -> int:
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    return int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or hwnd)


def _primer_plano_es_el_cuadro(cuadro_hwnd: int) -> bool:
    """Si la ventana con el foco es ESTE cuadro (o un hijo suyo).

    Que el primer plano sea del mismo proceso no basta: la ventana principal
    de Desktop tambien lo es, y teclear ahi mete la ruta en el lienzo.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.GetForegroundWindow.restype = wintypes.HWND
    frente = user32.GetForegroundWindow()
    if not frente:
        return False
    return _raiz_de(int(frente)) == int(cuadro_hwnd)


def _primer_plano_es_de(pid: int) -> bool:
    """Si la ventana con el foco pertenece al proceso verificado.

    Es la comprobacion que falta antes de TECLEAR: `SendInput` entrega las
    teclas a quien tenga el foco, y con varias ventanas de Desktop abiertas
    ese foco cambia de manos en mitad de la ruta. Tecleado a medias y en otro
    sitio: eso era `expected_len=66, actual_len=17`.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.GetForegroundWindow.restype = wintypes.HWND
    frente = user32.GetForegroundWindow()
    if not frente:
        return False
    return _duenio_de_ventana(int(frente)) == int(pid)


def clic_dinamico(punto, dialogo_hwnd: int, pid_esperado: int) -> Dict[str, Any]:
    """Un clic real en un punto CALCULADO del elemento, nunca memorizado.

    Se exige, en este orden: que el punto caiga dentro del rectangulo del
    cuadro de guardado, y que la ventana que hay en ese punto pertenezca al
    proceso verificado. Si algo no cuadra, no se pulsa: un clic en las
    coordenadas equivocadas cae en la aplicacion de otra persona.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    x, y = int(punto[0]), int(punto[1])
    rect = _rect_ventana(dialogo_hwnd)
    if not (rect.left <= x <= rect.right and rect.top <= y <= rect.bottom):
        raise HelperError(
            "clic", "El punto calculado cae fuera del cuadro de guardado.",
            point=[x, y], dialog_rect=[rect.left, rect.top, rect.right,
                                       rect.bottom])

    user32 = _user32()
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    punto_win = wintypes.POINT(x, y)
    bajo_cursor = user32.WindowFromPoint(punto_win)
    duenio = wintypes.DWORD()
    user32.GetWindowThreadProcessId(bajo_cursor, ctypes.byref(duenio))
    if int(duenio.value) != int(pid_esperado):
        raise HelperError(
            "clic", "La ventana bajo el punto no es del proceso verificado.",
            point=[x, y], owner_pid=int(duenio.value),
            expected_pid=int(pid_esperado))

    ancho = user32.GetSystemMetrics(0)
    alto = user32.GetSystemMetrics(1)
    INPUT, MOUSEINPUT, _KI = _estructuras_input()

    def _raton(banderas, ax=0, ay=0):
        entrada = INPUT()
        entrada.type = INPUT_MOUSE
        entrada.mi = MOUSEINPUT(dx=ax, dy=ay, mouseData=0, dwFlags=banderas,
                                time=0, dwExtraInfo=None)
        return entrada

    absx = int(x * 65535 / max(1, ancho - 1))
    absy = int(y * 65535 / max(1, alto - 1))
    _enviar_teclas([_raton(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, absx, absy)])
    time.sleep(0.12)
    _enviar_teclas([_raton(MOUSEEVENTF_LEFTDOWN)])
    time.sleep(0.06)
    _enviar_teclas([_raton(MOUSEEVENTF_LEFTUP)])
    return {"point": [x, y], "owner_pid": int(duenio.value)}


# --------------------------------------------------------------------- UIA --
def _coleccion(buscar) -> List[Any]:
    """Materializa un `FindAll` tolerando el puntero NULO de UI Automation."""
    try:
        encontrados = buscar()
    except Exception:                                     # noqa: BLE001
        return []
    if not encontrados:
        return []
    try:
        total = int(encontrados.Length)
    except Exception:                                     # noqa: BLE001
        return []
    salida = []
    for i in range(total):
        try:
            salida.append(encontrados.GetElement(i))
        except Exception:                                 # noqa: BLE001
            break
    return salida


class Uia:
    CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

    def __init__(self):
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0                # COINIT_MULTITHREADED
        import comtypes.client

        self.modulo = comtypes.client.GetModule("UIAutomationCore.dll")
        self.auto = comtypes.client.CreateObject(
            self.CLSID, interface=self.modulo.IUIAutomation)

    def desde_hwnd(self, hwnd: int):
        return self.auto.ElementFromHandle(hwnd)

    def por_id(self, raiz, automation_id: str, tipo: int):
        """El control con ese id, o **None** cuando no hay ninguno.

        `FindFirst` no devuelve `None` al no encontrar nada: devuelve un
        puntero COM NULO, que no es `None` y revienta al usarlo. Costo una
        exportacion a `.pbit` entera: el dialogo de plantilla no pone
        AutomationId a sus botones, `por_id` devolvia ese nulo, el `is None`
        de quien llamaba no lo veia y el `Invoke` moria con
        `ValueError: NULL COM pointer access`. Se normaliza aqui, una vez.
        """
        condicion = self.auto.CreateAndCondition(
            self.auto.CreatePropertyCondition(UIA_PROP_AUTOID, automation_id),
            self.auto.CreatePropertyCondition(UIA_PROP_CONTROLTYPE, tipo))
        try:
            encontrado = raiz.FindFirst(UIA_SCOPE_DESC, condicion)
        except Exception:                                 # noqa: BLE001
            return None
        return encontrado if encontrado else None

    def todos_de_tipo(self, raiz, tipo: int) -> List[Any]:
        """Todos los elementos de ese tipo. Lista vacia si no hay ninguno.

        `FindAll` puede devolver un puntero COM NULO cuando no encuentra
        nada, y tocarlo revienta con `ValueError: NULL COM pointer access`.
        Paso contra Power BI Desktop real al buscar el boton del dialogo de
        plantilla, y tumbo la exportacion despues de haber guardado. "No hay
        ninguno" es la lectura correcta, no un error.
        """
        return _coleccion(lambda: raiz.FindAll(
            UIA_SCOPE_DESC,
            self.auto.CreatePropertyCondition(UIA_PROP_CONTROLTYPE, tipo)))

    def por_nombre(self, raiz, patron: "re.Pattern[str]",
                   tipos: Optional[List[int]] = None) -> List[Any]:
        """Elementos cuyo nombre accesible cumple el patron, por tipo.

        La condicion de propiedad de UIA compara el nombre exacto y sin
        ignorar mayusculas; aqui hace falta una expresion regular por idioma,
        asi que se enumeran los tipos y se filtra en Python.
        """
        salida: List[Any] = []
        for tipo in (tipos or [UIA_TIPO_BUTTON, UIA_TIPO_MENUITEM,
                               UIA_TIPO_LISTITEM, UIA_TIPO_RADIOBUTTON,
                               UIA_TIPO_CHECKBOX, UIA_TIPO_SPLITBUTTON,
                               UIA_TIPO_TABITEM]):
            for elemento in self.todos_de_tipo(raiz, tipo):
                nombre = self.nombre(elemento)
                if nombre and patron.search(nombre):
                    salida.append(elemento)
        return salida

    def nombre(self, elemento) -> str:
        try:
            return str(elemento.CurrentName or "")
        except Exception:                                 # noqa: BLE001
            return ""

    def textos(self, raiz, maximo: int = 400) -> str:
        """El texto estatico de un dialogo (para clasificarlo), acotado."""
        try:
            partes = [self.nombre(e) for e in self.todos_de_tipo(raiz, UIA_TIPO_TEXT)]
        except Exception:                                 # noqa: BLE001
            return ""
        return " ".join(p for p in partes if p)[:maximo]

    def foco_dentro_de(self, elemento) -> Optional[bool]:
        """Si el elemento con foco es `elemento` o cuelga de el. None si no se sabe."""
        try:
            enfocado = self.auto.GetFocusedElement()
            caminante = self.auto.ControlViewWalker
            actual = enfocado
            for _ in range(8):
                if actual is None:
                    return False
                if self.auto.CompareElements(actual, elemento):
                    return True
                actual = caminante.GetParentElement(actual)
            return False
        except Exception:                                 # noqa: BLE001
            return None

    def valor(self, elemento) -> Optional[str]:
        try:
            return elemento.GetCurrentPattern(UIA_PAT_VALOR).QueryInterface(
                self.modulo.IUIAutomationValuePattern).CurrentValue
        except Exception:                                 # noqa: BLE001
            return None

    def fijar_valor(self, elemento, texto: str) -> bool:
        """`ValuePattern.SetValue`: pone el texto sin pasar por el teclado.

        Es la via preferida para la ruta: no depende de quien tenga el foco ni
        de la cola de entrada del sistema, que son las dos cosas que se
        rompian con varias ventanas abiertas. Devuelve False si el control no
        lo soporta o esta en solo lectura; entonces se teclea.
        """
        try:
            patron = elemento.GetCurrentPattern(UIA_PAT_VALOR).QueryInterface(
                self.modulo.IUIAutomationValuePattern)
        except Exception:                                 # noqa: BLE001
            return False
        try:
            if bool(patron.CurrentIsReadOnly):
                return False
        except Exception:                                 # noqa: BLE001
            pass
        try:
            patron.SetValue(texto)
            return True
        except Exception:                                 # noqa: BLE001
            return False

    def expandir(self, combo):
        combo.GetCurrentPattern(UIA_PAT_EXPANDIR).QueryInterface(
            self.modulo.IUIAutomationExpandCollapsePattern).Expand()

    def colapsar(self, combo) -> bool:
        try:
            combo.GetCurrentPattern(UIA_PAT_EXPANDIR).QueryInterface(
                self.modulo.IUIAutomationExpandCollapsePattern).Collapse()
            return True
        except Exception:                                 # noqa: BLE001
            return False

    def estado_expandido(self, combo) -> Optional[int]:
        try:
            return int(combo.GetCurrentPattern(UIA_PAT_EXPANDIR).QueryInterface(
                self.modulo.IUIAutomationExpandCollapsePattern
            ).CurrentExpandCollapseState)
        except Exception:                                 # noqa: BLE001
            return None

    def items(self, combo) -> List[Any]:
        return _coleccion(lambda: combo.FindAll(
            UIA_SCOPE_DESC, self.auto.CreatePropertyCondition(
                UIA_PROP_CONTROLTYPE, UIA_TIPO_LISTITEM)))

    def invocar(self, elemento) -> str:
        """Invoke -> DoDefaultAction. Devuelve por cual salio.

        Los patrones tambien llegan como puntero NULO cuando el elemento no
        los soporta, asi que se comprueban antes de usarlos en vez de
        confiar en que la excepcion sea de un tipo concreto.
        """
        patron = None
        try:
            patron = elemento.GetCurrentPattern(UIA_PAT_INVOCAR)
        except Exception:                                 # noqa: BLE001
            patron = None
        if patron:
            try:
                patron.QueryInterface(
                    self.modulo.IUIAutomationInvokePattern).Invoke()
                return "invoke"
            except Exception:                             # noqa: BLE001
                pass
        legado = elemento.GetCurrentPattern(UIA_PAT_LEGACY)
        if not legado:
            raise HelperError(
                "interfaz", "El control no admite ninguna forma de activarse "
                "(ni Invoke ni la accion por defecto).",
                reason="element_not_invokable")
        legado.QueryInterface(
            self.modulo.IUIAutomationLegacyIAccessiblePattern
        ).DoDefaultAction()
        return "legacy_default_action"

    def seleccionar(self, elemento) -> str:
        """SelectionItem.Select -> Invoke -> DoDefaultAction, en ese orden."""
        try:
            elemento.GetCurrentPattern(UIA_PAT_SELITEM).QueryInterface(
                self.modulo.IUIAutomationSelectionItemPattern).Select()
            return "selection_item"
        except Exception:                                 # noqa: BLE001
            return self.invocar(elemento)

    def esta_seleccionado(self, elemento) -> Optional[bool]:
        """`IsSelected` del patron SelectionItem, o None si no lo expone."""
        try:
            return bool(elemento.GetCurrentPattern(UIA_PAT_SELITEM).QueryInterface(
                self.modulo.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        except Exception:                                 # noqa: BLE001
            return None

    def estado_toggle(self, elemento) -> Optional[int]:
        """ToggleState (0 off, 1 on, 2 indeterminado), o None si no lo expone."""
        try:
            return int(elemento.GetCurrentPattern(UIA_PAT_TOGGLE).QueryInterface(
                self.modulo.IUIAutomationTogglePattern).CurrentToggleState)
        except Exception:                                 # noqa: BLE001
            return None

    def punto_clicable(self, elemento):
        """`GetClickablePoint` y, si no lo da, el centro de su rectangulo.

        Los dos valores se calculan EN EL MOMENTO a partir del propio
        elemento: no hay ninguna coordenada escrita en el codigo.
        """
        try:
            ok, punto = elemento.GetClickablePoint()
            if ok:
                return (punto.x, punto.y)
        except Exception:                                 # noqa: BLE001
            pass
        rect = elemento.CurrentBoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def enfocar(self, elemento) -> bool:
        try:
            elemento.SetFocus()
            return True
        except Exception:                                 # noqa: BLE001
            return False


# ------------------------------------------------------------- identidad ----
def verificar_proceso(pid: int, arranque: Optional[float]) -> Dict[str, Any]:
    """Nombre y hora de arranque. Windows recicla PIDs; el numero no basta."""
    import psutil

    try:
        proceso = psutil.Process(int(pid))
        nombre = (proceso.name() or "").casefold()
        creado = float(proceso.create_time())
    except Exception as exc:                              # noqa: BLE001
        raise HelperError("identidad",
                          "El proceso de Power BI Desktop no se puede "
                          "inspeccionar.", pid=pid,
                          cause=type(exc).__name__) from exc
    if nombre != "pbidesktop.exe":
        raise HelperError("identidad",
                          "Ese PID ya no es Power BI Desktop.", pid=pid,
                          actual_process=nombre)
    if arranque is not None and abs(creado - float(arranque)) > 1.0:
        raise HelperError("identidad",
                          "El PID se reutilizo: otra hora de arranque.",
                          pid=pid, expected_started=arranque,
                          actual_started=creado)
    return {"pid": int(pid), "create_time": creado}


def ventanas_de(pid: int) -> List[Dict[str, Any]]:
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    salida: List[Dict[str, Any]] = []

    @callback
    def visita(hwnd, _lparam):
        duenio = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(duenio))
        if int(duenio.value) != int(pid) or not user32.IsWindowVisible(hwnd):
            return True
        clase = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, clase, 256)
        titulo = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, titulo, 512)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        salida.append({"hwnd": int(hwnd), "class": clase.value,
                       "title": titulo.value,
                       "width": int(rect.right - rect.left),
                       "height": int(rect.bottom - rect.top)})
        return True

    user32.EnumWindows(visita, 0)
    return salida


def traer_al_frente(hwnd: int, pid: int) -> bool:
    """Primer plano por la via documentada, y COMPROBADO despues.

    `SetForegroundWindow` a secas lo ignora Windows cuando quien llama no es
    el duenio del foco -un servidor lanzado desde consola nunca lo es-, y no
    falla ruidosamente: solo parpadea el boton de la barra de tareas.
    """
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                         wintypes.BOOL]
    propio = int(kernel32.GetCurrentThreadId())

    for _ in range(25):
        frente = user32.GetForegroundWindow()
        duenio = wintypes.DWORD()
        hilo_frente = user32.GetWindowThreadProcessId(frente,
                                                      ctypes.byref(duenio))
        if int(duenio.value) == int(pid):
            return True
        adjunto = False
        try:
            if hilo_frente and hilo_frente != propio:
                adjunto = bool(user32.AttachThreadInput(
                    wintypes.DWORD(propio), wintypes.DWORD(hilo_frente), True))
            user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))
            user32.ShowWindow(wintypes.HWND(hwnd), 9)          # SW_RESTORE
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        finally:
            if adjunto:
                user32.AttachThreadInput(
                    wintypes.DWORD(propio), wintypes.DWORD(hilo_frente), False)
        time.sleep(0.15)
    return False


# ------------------------------------------------------------- la secuencia --
def _ventana_principal(pid: int) -> Dict[str, Any]:
    candidatas = [v for v in ventanas_de(pid)
                  if v["title"].strip()
                  and v["class"].casefold() != CLASE_DIALOGO.casefold()]
    if len(candidatas) != 1:
        raise HelperError("ventana",
                          "No hay exactamente una ventana principal en ese "
                          "proceso.", pid=pid,
                          windows=[{"hwnd": v["hwnd"],
                                    "title": _redactar(v["title"], 60)}
                                   for v in candidatas])
    return candidatas[0]


def _esperar_cuadro(uia: Uia, pid: int, plazo: float) -> Dict[str, Any]:
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        for ventana in ventanas_de(pid):
            if ventana["class"].casefold() != CLASE_DIALOGO.casefold():
                continue
            elemento = uia.desde_hwnd(ventana["hwnd"])
            if (uia.por_id(elemento, AUTOMATION_ID_NOMBRE, UIA_TIPO_COMBOBOX)
                    and uia.por_id(elemento, AUTOMATION_ID_TIPO,
                                   UIA_TIPO_COMBOBOX)):
                return ventana
        time.sleep(0.4)
    raise HelperError("abrir_cuadro",
                      "No aparecio el cuadro de guardado en el plazo.",
                      pid=pid, timeout=plazo)


def _hasta_que(condicion, *, plazo: float, cada: float = 0.05):
    """Espera a que algo SEA cierto, no a que pase un rato.

    Un `sleep` fijo tras inyectar teclas es adivinar: en una maquina ociosa
    sobra y en una ocupada no llega, y el sintoma es un campo leido a medias
    -76 caracteres pedidos, 31 leidos- que parece un fallo de escritura y es
    un fallo de sincronizacion. Aqui se pregunta por el estado real hasta que
    cuadra, y el plazo agotado acusa a la sincronizacion, no a la aplicacion.

    Devuelve el ultimo valor observado, cumpla o no: quien llama decide que
    hacer con el y puede contarlo en el error.
    """
    limite = time.monotonic() + plazo
    valor = None
    while True:
        valor = condicion()
        if valor is not None:
            return valor
        if time.monotonic() >= limite:
            return None
        time.sleep(cada)


def _error_com_como_helper(exc: BaseException, fase: str) -> Optional[HelperError]:
    """Traduce un `COMError` a HelperError; los transitorios se reintentan.

    Solo los HRESULT de la lista se marcan transitorios. Un COMError distinto
    sigue siendo HelperError -para que el cuadro se limpie y el padre reciba
    fase y detalle- pero NO se reintenta. Cualquier otra excepcion no se
    toca: un fallo de programacion no se disfraza de carrera.
    """
    hresult = getattr(exc, "hresult", None)
    if hresult is None or type(exc).__name__ != "COMError":
        return None
    codigo = int(hresult) & 0xFFFFFFFF
    transitorio = codigo in HRESULT_TRANSITORIOS
    return HelperError(
        fase,
        "La interfaz cambio debajo de la automatizacion (elemento no "
        "disponible)." if transitorio else
        f"UI Automation devolvio un error COM (0x{codigo:08X}).",
        transitoria=transitorio,
        reason="ui_element_gone" if transitorio else "ui_com_error",
        hresult=f"0x{codigo:08X}", cause=str(exc)[:160])


def _con_intentos(fase: str, intento: Callable[[int], Dict[str, Any]], *,
                  intentos: int = INTENTOS_POR_FASE,
                  plazo: float = PLAZO_POR_FASE) -> Dict[str, Any]:
    """Repite una fase mientras su fallo sea transitorio, con tope de tiempo.

    Cada intento vuelve a LOCALIZAR sus controles: la funcion `intento`
    recibe el numero de intento y parte de cero, sin reutilizar referencias
    UIA de la vuelta anterior, que es justo lo que caduca cuando la interfaz
    se repinta. El registro de intentos viaja en el resultado -o en el error-
    para que el padre pueda contar que paso y cuanto se espero.
    """
    inicio = time.monotonic()
    registro: List[Dict[str, Any]] = []
    ultimo: Optional[HelperError] = None
    for numero in range(1, max(1, intentos) + 1):
        arranque = time.monotonic()
        try:
            try:
                resultado = intento(numero)
            except HelperError:
                raise
            except Exception as exc:                      # noqa: BLE001
                traducido = _error_com_como_helper(exc, fase)
                if traducido is None:
                    raise
                raise traducido from exc
        except HelperError as exc:
            registro.append({
                "attempt": numero, "ok": False, "transient": exc.transitoria,
                "reason": exc.detalles.get("reason"),
                "error": _redactar(exc.mensaje, 160),
                "seconds": round(time.monotonic() - arranque, 2)})
            ultimo = exc
            if not exc.transitoria:
                break
            restante = plazo - (time.monotonic() - inicio)
            if numero >= intentos or restante <= 0:
                break
            pausa = BACKOFF_ENTRE_INTENTOS[
                min(numero - 1, len(BACKOFF_ENTRE_INTENTOS) - 1)]
            time.sleep(min(pausa, restante))
            continue
        registro.append({"attempt": numero, "ok": True,
                         "seconds": round(time.monotonic() - arranque, 2)})
        resultado = dict(resultado)
        resultado["attempts"] = registro
        resultado["attempts_total"] = len(registro)
        return resultado
    assert ultimo is not None
    ultimo.detalles["attempts"] = registro
    ultimo.detalles["attempts_total"] = len(registro)
    ultimo.detalles["phase_seconds"] = round(time.monotonic() - inicio, 2)
    ultimo.detalles.setdefault("phase", fase)
    raise ultimo


def _localizar(uia: Uia, dialogo_hwnd: int, automation_id: str, tipo: int, *,
               fase: str, que: str):
    """Localiza un control DEL CUADRO, cada vez de nuevo.

    Que no aparezca es transitorio: el cuadro comun de archivo monta sus
    controles por partes y un `FindFirst` demasiado temprano devuelve nada.
    """
    try:
        elemento = uia.desde_hwnd(dialogo_hwnd)
    except Exception as exc:                              # noqa: BLE001
        raise HelperError(fase, "El cuadro de guardado no responde a UI "
                          "Automation.", transitoria=True,
                          reason="dialog_unreachable",
                          cause=type(exc).__name__) from exc
    control = uia.por_id(elemento, automation_id, tipo)
    if control is None:
        raise HelperError(fase, f"El cuadro no expone {que}.",
                          transitoria=True, reason="control_missing",
                          automation_id=automation_id)
    return control


def _elegir_tipo(uia: Uia, dialogo_hwnd: int, extension: str) -> Dict[str, Any]:
    """El tipo se ELIGE y se comprueba que la aplicacion lo proceso.

    `Select()` cambia lo que se ve y deja la lista abierta; `Invoke()` ejecuta
    la accion por defecto del elemento y la lista se cierra sola. Que se
    cierre es la señal de que hubo compromiso, no solo repintado.

    Dos fallos que antes se confundian: una lista que AUN no se poblo -que se
    reintenta, localizando el desplegable otra vez- y una lista poblada que de
    verdad no ofrece el formato, que es definitivo y trae lo que si ofrece.
    """
    objetivo = extension.casefold().lstrip("*")

    def intento(_numero: int) -> Dict[str, Any]:
        combo = _localizar(uia, dialogo_hwnd, AUTOMATION_ID_TIPO,
                           UIA_TIPO_COMBOBOX, fase="tipo",
                           que="el desplegable de tipo")
        previo = uia.valor(combo)
        uia.expandir(combo)
        # La lista tarda en poblarse; se espera A QUE HAYA opciones, no un rato.
        opciones = _hasta_que(lambda: (uia.items(combo) or None),
                              plazo=ESPERA_INTERFAZ) or []
        if not opciones:
            _colapsar(uia, combo)
            raise HelperError(
                "tipo", "El desplegable de tipo no llego a poblarse: no se "
                "puede saber que formatos ofrece.", transitoria=True,
                reason="file_type_list_not_loaded", list_loaded=False,
                available=[], current=previo, waited=ESPERA_INTERFAZ)
        nombres = [o.CurrentName for o in opciones]
        elegido = next((o for o in opciones
                        if objetivo in (o.CurrentName or "").casefold()), None)
        if elegido is None:
            _colapsar(uia, combo)
            raise HelperError("tipo", f"El cuadro no ofrece '{extension}'.",
                              reason="file_type_not_offered", list_loaded=True,
                              available=nombres, current=previo)
        nombre = elegido.CurrentName
        via = uia.invocar(elegido)
        # Que la lista se cierre es la señal de que se proceso: se espera a ESO.
        estado = _hasta_que(
            lambda: (uia.estado_expandido(combo)
                     if uia.estado_expandido(combo) in (ESTADO_CERRADO, None)
                     else None),
            plazo=ESPERA_INTERFAZ)
        if estado is None:
            estado = uia.estado_expandido(combo)
        if estado not in (ESTADO_CERRADO, None):
            _colapsar(uia, combo)
            raise HelperError(
                "tipo", "El desplegable sigue abierto tras activar la opcion: "
                "la aplicacion no proceso la eleccion.", transitoria=True,
                reason="selection_not_committed",
                expand_collapse_state=estado, via=via, requested=extension)
        actual = uia.valor(combo)
        if actual and objetivo not in actual.casefold():
            raise HelperError("tipo", "El tipo no quedo en lo pedido.",
                              transitoria=True, reason="wrong_type_after_select",
                              current=actual, requested=extension)
        return {"file_type_selected": nombre, "via": via, "previous": previo,
                "expand_state_after": estado, "available": nombres}

    return _con_intentos("tipo", intento)


def _leer_campo(uia: Uia, campo) -> str:
    return (uia.valor(campo) or "").strip('"')


#: Identificador del `Edit` del nombre dentro del ComboBox del cuadro comun.
IDC_NOMBRE_EDIT = 0x03E9


def _edit_del_nombre(dialogo_hwnd: int) -> Optional[int]:
    """El `Edit` id 1001 que cuelga de un `ComboBox`: el campo de verdad.

    El identificador solo no basta -la barra de direcciones tiene otro `Edit`
    dentro de otro `ComboBox`-, y la pareja (id 1001 + padre ComboBox) es
    unica en el cuadro.
    """
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    # `_user32()` devuelve una WinDLL nueva en cada llamada, asi que los
    # tipos se declaran aqui: sin ellos ctypes adivina, y un HWND de 64 bits
    # no cabe en el `int` que supone por defecto.
    user32.EnumChildWindows.argtypes = [
        wintypes.HWND, ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                          wintypes.LPARAM), wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user32.GetDlgCtrlID.restype = ctypes.c_int
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hijos: List[Dict[str, Any]] = []

    @callback
    def visita(handle, _lparam):
        clase = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, clase, 256)
        hijos.append({"hwnd": int(handle), "class": clase.value,
                      "id": int(user32.GetDlgCtrlID(handle)),
                      "parent": int(user32.GetParent(handle) or 0)})
        return True

    user32.EnumChildWindows(wintypes.HWND(dialogo_hwnd), visita, 0)
    combos = {c["hwnd"] for c in hijos if c["class"] == "ComboBox"}
    for control in hijos:
        if (control["class"] == "Edit" and control["id"] == IDC_NOMBRE_EDIT
                and control["parent"] in combos):
            return control["hwnd"]
    return None


def _texto_win32(hwnd: int) -> str:
    """`WM_GETTEXT` sobre un control, con los tipos DECLARADOS.

    Sin `argtypes`, ctypes convierte el puntero del buffer con su suposicion
    por defecto y en 64 bits revienta con `int too long to convert`. Se vio
    en la primera prueba contra Desktop real.
    """
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    largo = int(user32.SendMessageW(wintypes.HWND(hwnd), 0x000E, 0, 0))
    if largo <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(largo + 1)
    user32.SendMessageW(wintypes.HWND(hwnd), 0x000D, largo + 1,
                        ctypes.cast(buffer, ctypes.c_void_p).value)
    return buffer.value


def _nombre_comprometido(dialogo_hwnd: int, ruta: str) -> Optional[bool]:
    """Si el `Edit` del cuadro CONTIENE la ruta, leido por Win32.

    Es la lectura mas cercana al cuadro que se puede hacer desde fuera, pero
    **no demuestra por si sola que el cuadro vaya a guardar ahi**: se midio
    contra Power BI Desktop real que `ValuePattern.SetValue` deja el texto en
    el `Edit` -UIA y `WM_GETTEXT` devolvian los 133 caracteres pedidos- y aun
    asi el guardado salio con el nombre y la carpeta por defecto. El cuadro
    moderno lleva su propio estado y solo lo actualiza con las notificaciones
    que genera la escritura real.

    Por eso esta lectura solo se usa DESPUES de teclear, que es lo que si
    produce esas notificaciones. `None` = no se pudo leer el control.
    """
    edit = _edit_del_nombre(dialogo_hwnd)
    if edit is None:
        return None
    return _texto_win32(edit).strip('"') == ruta


def _colapsar(uia: Uia, combo) -> None:
    """Deja la lista cerrada antes de reintentar, si el cliente lo permite."""
    colapsar = getattr(uia, "colapsar", None)
    if colapsar is not None:
        colapsar(combo)


def _escribir_ruta(uia: Uia, dialogo_hwnd: int, ruta: str,
                   pid: Optional[int] = None) -> Dict[str, Any]:
    """Ruta ABSOLUTA en el campo del nombre, TECLEADA y releida.

    Por que solo se teclea
    ----------------------
    `ValuePattern.SetValue` parecia la via limpia -no depende del foco ni de
    la cola de teclado- y **no sirve**: contra Power BI Desktop real deja el
    texto en el `Edit` (UIA y `WM_GETTEXT` devolvieron los 133 caracteres
    pedidos) y el cuadro guarda igualmente con SU nombre y SU carpeta por
    defecto. Se midio: se pidio la ruta larga de otra carpeta y
    aparecio `Demo.pbix` junto al proyecto. El cuadro moderno lleva su propio
    estado y solo lo actualiza con las notificaciones que produce la
    escritura real. Es el mismo fallo que `CB_SETCURSEL` con el tipo, y por
    eso se resuelve igual: no se usa.

    Lo que si falla del tecleo es la VELOCIDAD. Teclear tan rapido como
    permite `SendInput` pierde caracteres -133 pedidos, 26 recibidos, medido
    en vivo-, que es el `expected_len=182, actual_len=30` del informe. Cada
    intento teclea mas despacio que el anterior.

    Y no se teclea a ciegas: hace falta el foco en ESTE cuadro -no en otra
    ventana del mismo proceso- y, si UIA lo expone, en ESTE campo.
    """

    def intento(numero: int) -> Dict[str, Any]:
        campo = _localizar(uia, dialogo_hwnd, AUTOMATION_ID_NOMBRE,
                           UIA_TIPO_COMBOBOX, fase="nombre",
                           que="el campo del nombre")
        metodos: List[str] = ["keyboard"]

        if pid is not None and not _primer_plano_es_el_cuadro(dialogo_hwnd):
            traer_al_frente(dialogo_hwnd, pid)
            if not _primer_plano_es_el_cuadro(dialogo_hwnd):
                raise HelperError(
                    "nombre", "El cuadro de guardado no tiene el foco y no se "
                    "pudo recuperar; no se teclea en otra ventana, ni siquiera "
                    "en la principal de Desktop.", transitoria=True,
                    reason="focus_lost", methods_tried=metodos)
        uia.enfocar(campo)
        time.sleep(0.3)
        foco_en_campo = getattr(uia, "foco_dentro_de", lambda e: None)(campo)
        if foco_en_campo is False:
            raise HelperError(
                "nombre", "El foco no quedo en el campo del nombre; no se "
                "teclea la ruta en otro control.", transitoria=True,
                reason="field_focus_lost", methods_tried=metodos)
        tanda, pausa = CADENCIAS_TECLEO[min(numero - 1,
                                            len(CADENCIAS_TECLEO) - 1)]
        seleccionar_todo()
        escribir_texto_real(ruta, tanda=tanda, pausa=pausa)

        # Las teclas sinteticas llegan a la cola del sistema al instante, pero
        # la aplicacion las consume a su ritmo. Se espera a que el campo
        # CONTENGA la ruta, en vez de a que pase medio segundo. La lectura
        # que manda es la de Win32 -lo que el cuadro usara al confirmar-; la
        # de UIA solo se acepta si el `Edit` no se pudo localizar.
        fuente = {"quien": "uia"}

        def _quedo() -> Optional[bool]:
            por_win32 = _nombre_comprometido(dialogo_hwnd, ruta)
            if por_win32 is not None:
                fuente["quien"] = "win32"
                return True if por_win32 else None
            fuente["quien"] = "uia"
            return True if _leer_campo(uia, campo) == ruta else None

        escrito = _hasta_que(_quedo, plazo=ESPERA_INTERFAZ)
        if escrito is None:
            edit = _edit_del_nombre(dialogo_hwnd)
            parcial = (_texto_win32(edit).strip('"') if edit
                       else _leer_campo(uia, campo))
            a_medias = bool(parcial) and ruta.startswith(parcial) \
                and len(parcial) < len(ruta)
            raise HelperError(
                "nombre", "El campo del nombre no quedo con la ruta pedida.",
                transitoria=True,
                reason="partial_write" if a_medias else "value_mismatch",
                expected_len=len(ruta), actual_len=len(parcial),
                verified_by=fuente["quien"], typing_batch=tanda,
                waited=ESPERA_INTERFAZ, methods_tried=metodos)
        return {"filename_verified": True, "length": len(ruta),
                "method": "keyboard", "verified_by": fuente["quien"],
                "typing_batch": tanda, "methods_tried": metodos}

    return _con_intentos("nombre", intento)


def _confirmar(uia: Uia, dialogo_hwnd: int, pid: int) -> Dict[str, Any]:
    """Invoke -> DoDefaultAction -> clic real calculado. En ese orden.

    Contra Power BI Desktop real **el primero basta**: el cuadro se cierra y el
    archivo aparece, y eso es lo que devuelve `commit_method`. Los otros dos
    siguen ahi porque cual funciona depende de la version y del tema visual de
    Windows, y descubrirlo en la maquina de otra persona -con el guardado a
    medias- sale caro.

    Que el cuadro se cierre es evidencia fuerte, no prueba: la prueba es el
    archivo, y de eso se encarga quien llama. El punto del clic se calcula del
    propio elemento en el momento; no hay coordenadas escritas ni supuestos
    sobre DPI, monitor o escala.
    """
    elemento = uia.desde_hwnd(dialogo_hwnd)
    boton = uia.por_id(elemento, AUTOMATION_ID_GUARDAR, UIA_TIPO_BUTTON)
    if boton is None:
        raise HelperError("guardar", "El cuadro no expone su boton Guardar.",
                          automation_id=AUTOMATION_ID_GUARDAR)
    if boton.CurrentAutomationId != AUTOMATION_ID_GUARDAR:
        raise HelperError("guardar", "El boton hallado no es el esperado.",
                          automation_id=boton.CurrentAutomationId)

    intentos: List[Dict[str, Any]] = []
    try:
        via = uia.invocar(boton)
        intentos.append({"method": via, "raised": False})
    except Exception as exc:                              # noqa: BLE001
        intentos.append({"method": "invoke", "raised": type(exc).__name__})

    # ¿Se cerro el cuadro? Si si, el patron basto y no se toca el raton. Se
    # espera A QUE se cierre, no un rato fijo.
    cerrado = _hasta_que(
        lambda: (True if not _cuadro_sigue_abierto(dialogo_hwnd) else None),
        plazo=1.5, cada=0.1)
    if cerrado:
        return {"commit_method": intentos[-1]["method"], "attempts": intentos}

    punto = uia.punto_clicable(boton)
    if punto is None:
        raise HelperError("guardar",
                          "El boton no expone un punto sobre el que pulsar.",
                          attempts=intentos)
    if not traer_al_frente(dialogo_hwnd, pid):
        raise HelperError("guardar",
                          "No se pudo poner el cuadro al frente; no se hace "
                          "clic a ciegas.", attempts=intentos)
    detalle = clic_dinamico(punto, dialogo_hwnd, pid)
    intentos.append({"method": "dynamic_click", **detalle})
    return {"commit_method": "dynamic_click", "attempts": intentos}


def _cuadro_sigue_abierto(hwnd: int) -> bool:
    import ctypes.wintypes
    wintypes = ctypes.wintypes

    user32 = _user32()
    user32.IsWindow.argtypes = [wintypes.HWND]
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def _esperar_cierre(hwnd: int, plazo: float) -> bool:
    limite = time.monotonic() + plazo
    while True:
        if not _cuadro_sigue_abierto(hwnd):
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(0.3)


def _archivo_aparecio(ruta: str, desde: float) -> bool:
    """Si el destino existe y es de ESTA ejecucion (mtime posterior)."""
    try:
        return os.path.isfile(ruta) and os.path.getmtime(ruta) >= desde - 2
    except OSError:
        return False


def _estado_del_guardado(hwnd: int, ruta: str, desde: float) -> Dict[str, Any]:
    """Lo que ya OCURRIO, antes de plantearse repetir una confirmacion."""
    return {"dialog_closed": not _cuadro_sigue_abierto(hwnd),
            "file_appeared": _archivo_aparecio(ruta, desde)}


def _aceptar_reemplazo(uia: Uia, modal: Dict[str, Any]) -> Dict[str, Any]:
    """Pulsa 'Si' en el "¿reemplazar?" que abrio NUESTRO cuadro.

    Solo se llama con `overwrite=true` del padre -que ya respaldo el destino-
    y con un modal clasificado como confirmacion de reemplazo y poseido por
    el cuadro de esta operacion. Se busca el boton por su id (IDYES) o por su
    nombre; nunca se hace un clic por coordenadas sobre un modal.
    """
    elemento = uia.desde_hwnd(modal["hwnd"])
    boton = uia.por_id(elemento, AUTOMATION_ID_SI, UIA_TIPO_BUTTON)
    if boton is None:
        candidatos = uia.por_nombre(elemento, NOMBRE_BOTON_SI, [UIA_TIPO_BUTTON])
        boton = candidatos[0] if candidatos else None
    if boton is None:
        return {"accepted": False, "hwnd": modal["hwnd"],
                "note": "el cuadro de reemplazo no expone su boton Si"}
    via = uia.invocar(boton)
    return {"accepted": True, "hwnd": modal["hwnd"], "via": via,
            "modal_closed": _esperar_cierre(modal["hwnd"], 5.0)}


def _confirmar_con_verificacion(uia: Uia, dialogo_hwnd: int, pid: int,
                                ruta: str, *, plazo: float, desde: float,
                                overwrite: bool = False,
                                excluir: Optional[List[int]] = None
                                ) -> Dict[str, Any]:
    """Pulsa Guardar y espera el cierre; repite SOLO si nada ocurrio.

    Una confirmacion de resultado incierto no se repite a ciegas: antes de
    volver a pulsar se comprueba si el cuadro ya se cerro, si el archivo ya
    aparecio y si hay un DIALOGO abierto. Con un dialogo abierto no se pulsa
    nada mas: es la explicacion, y pulsar Guardar otra vez -con el clic real
    de respaldo- podia caer sobre ese dialogo y confirmar lo que nadie pidio.

    La unica excepcion es el "¿reemplazar?" del propio cuadro cuando el padre
    llamo con `overwrite=true`: ese si se acepta, y se deja constancia.
    """
    limite = time.monotonic() + plazo
    # El cuadro propio va PRIMERO: es contra el que `_modales` decide que
    # dialogos le pertenecen.
    fuera = [dialogo_hwnd] + [h for h in (excluir or []) if h != dialogo_hwnd]
    intentos: List[Dict[str, Any]] = []
    confirmacion: Dict[str, Any] = {}
    ya_ocurrio: Optional[Dict[str, Any]] = None
    evidencia: Optional[Dict[str, Any]] = None
    bloqueo: Optional[List[Dict[str, Any]]] = None
    reemplazo: Optional[Dict[str, Any]] = None

    def _estado_o_modales(numero: int) -> Optional[str]:
        """'done' si ya paso algo, 'blocked' si hay un dialogo, None si nada."""
        nonlocal evidencia, bloqueo, reemplazo
        estado = _estado_del_guardado(dialogo_hwnd, ruta, desde)
        if estado["dialog_closed"] or estado["file_appeared"]:
            evidencia = {"attempt": numero, **estado}
            return "done"
        modales = _modales(uia, pid, fuera)
        if not modales:
            return None
        propios = [m for m in modales
                   if m.get("owned_by_dialog") and m.get("kind") == "confirm_replace"]
        if overwrite and len(propios) == 1 and reemplazo is None:
            reemplazo = _aceptar_reemplazo(uia, propios[0])
            if reemplazo.get("accepted"):
                return None                     # se sigue esperando el cierre
        bloqueo = modales
        return "blocked"

    for numero in range(1, INTENTOS_POR_FASE + 1):
        if numero > 1:
            # Solo se REPITE si consta que la pulsacion anterior no hizo nada
            # y no hay ningun dialogo en medio.
            veredicto = _estado_o_modales(numero)
            if veredicto == "done":
                ya_ocurrio = evidencia
                break
            if veredicto == "blocked":
                break
        confirmacion = _confirmar(uia, dialogo_hwnd, pid)
        intentos.append({"attempt": numero, **confirmacion})
        restante = limite - time.monotonic()
        if restante <= 0:
            break
        espera = (ESPERA_CIERRE_TRAS_CONFIRMAR if numero < INTENTOS_POR_FASE
                  else restante)
        if _esperar_cierre(dialogo_hwnd, min(espera, restante)):
            evidencia = {"attempt": numero, "dialog_closed": True,
                         "file_appeared": _archivo_aparecio(ruta, desde)}
            break
        veredicto = _estado_o_modales(numero)
        if veredicto == "done" or veredicto == "blocked":
            break
    cerrado = _esperar_cierre(dialogo_hwnd, max(0.0, limite - time.monotonic()))
    return {
        "commit_method": confirmacion.get("commit_method"),
        "attempts": intentos,
        "attempts_total": len(intentos),
        "commit_evidence": evidencia,
        "already_committed": ya_ocurrio,
        "blocking_modals": bloqueo,
        "overwrite_confirmed": reemplazo,
        "dialog_closed": cerrado,
    }


def _cancelar_cuadro(uia: Uia, dialogo_hwnd: int, pid: int) -> Dict[str, Any]:
    """Cierra SOLO el cuadro de esta operacion, y dice si lo consiguio.

    Un cuadro de guardado que se queda abierto tras un fallo bloquea la
    ventana de Desktop hasta que alguien lo atienda. Se pulsa su Cancelar
    -identificado por su id, dentro de ESE hwnd, que se comprueba que sigue
    siendo del proceso verificado- y se verifica el cierre. Nada de Escape al
    aire ni de cerrar "el dialogo que haya".
    """
    salida: Dict[str, Any] = {"attempted": False, "dialog_closed": None,
                              "method": None}
    if not _cuadro_sigue_abierto(dialogo_hwnd):
        salida["dialog_closed"] = True
        salida["note"] = "el cuadro ya estaba cerrado"
        return salida
    if _duenio_de_ventana(dialogo_hwnd) != int(pid):
        salida["dialog_closed"] = False
        salida["note"] = "la ventana ya no pertenece al proceso verificado"
        return salida
    salida["attempted"] = True
    try:
        elemento = uia.desde_hwnd(dialogo_hwnd)
        boton = uia.por_id(elemento, AUTOMATION_ID_CANCELAR, UIA_TIPO_BUTTON)
        if boton is None:
            salida["note"] = "el cuadro no expone su boton Cancelar"
        else:
            salida["method"] = uia.invocar(boton)
    except Exception as exc:                              # noqa: BLE001
        salida["error"] = type(exc).__name__
    salida["dialog_closed"] = _esperar_cierre(dialogo_hwnd, 3.0)
    return salida


def _modales(uia: Uia, pid: int, excluir: List[int]) -> List[Dict[str, Any]]:
    """Dialogos abiertos del proceso, con quien los posee y que dicen.

    Se miran TODAS las ventanas visibles con titulo del proceso, no solo las
    de clase `#32770`: los dialogos propios de Power BI Desktop son WPF y no
    llevan esa clase. Quedan fuera las ventanas de `excluir` -la principal y
    el cuadro de guardado- y cualquier otro cuadro de guardado.
    """
    from horizun_pbi_mcp.powerbi.desktop_ui import clasificar_modal

    fuera = set(excluir)
    # Por convencion, el PRIMER excluido es el cuadro de guardado de esta
    # operacion: solo contra el se decide `owned_by_dialog`.
    cuadro = excluir[0] if excluir else None
    salida = []
    for ventana in ventanas_de(pid):
        if ventana["hwnd"] in fuera or not (ventana.get("title") or "").strip():
            continue
        es_comun = ventana["class"].casefold() == CLASE_DIALOGO.casefold()
        # Un dialogo WPF de Desktop (plantilla, credenciales) es una ventana
        # POSEIDA por la principal; la principal no tiene propietaria y no
        # es un modal por mucho que tenga titulo.
        if not es_comun and not _propietaria(ventana["hwnd"]):
            continue
        try:
            elemento = uia.desde_hwnd(ventana["hwnd"])
            if uia.por_id(elemento, AUTOMATION_ID_TIPO, UIA_TIPO_COMBOBOX):
                continue                    # es otro cuadro de guardado
            texto = uia.textos(elemento) if hasattr(uia, "textos") else ""
        except Exception:                                 # noqa: BLE001
            texto = ""
        clase, _accion = clasificar_modal(ventana["title"], texto)
        salida.append({"hwnd": ventana["hwnd"],
                       "title": _redactar(ventana["title"], 120),
                       "text": _redactar(texto, 200),
                       "class": ventana["class"],
                       "kind": clase,
                       # Solo cuenta la cadena hasta EL cuadro de guardado de
                       # esta operacion; la ventana principal posee de todo.
                       "owned_by_dialog": bool(cuadro) and _pertenece_al_cuadro(
                           ventana["hwnd"], int(cuadro))})
    return salida


def _atender_dialogo_de_plantilla(uia: Uia, pid: int, excluir: List[int],
                                  plazo: float) -> Dict[str, Any]:
    """Al guardar como `.pbit`, Desktop pide la descripcion de la plantilla.

    Es un cuadro propio de Power BI -WPF, no un dialogo comun del sistema-
    con un texto opcional y un boton Aceptar. Se reconoce por su titulo en
    cualquier clase de ventana, se pulsa Aceptar sin escribir descripcion, y
    se comprueba que se cierre. Cualquier OTRO dialogo que aparezca no se
    toca: se devuelve como modal para que lo decida el padre.
    """
    salida: Dict[str, Any] = {"seen": False, "accepted": False,
                              "dialog_closed": None, "waited": 0.0}
    inicio = time.monotonic()
    limite = inicio + plazo
    while time.monotonic() < limite:
        for ventana in ventanas_de(pid):
            # Es un dialogo PROPIO de Desktop (WPF), no un cuadro comun: se
            # reconoce por el titulo, sea cual sea su clase de ventana.
            if ventana["hwnd"] in excluir:
                continue
            if not NOMBRE_DIALOGO_PLANTILLA.search(ventana["title"] or ""):
                continue
            salida["seen"] = True
            salida["title"] = _redactar(ventana["title"], 80)
            salida["class"] = ventana["class"]
            try:
                elemento = uia.desde_hwnd(ventana["hwnd"])
                boton = uia.por_id(elemento, AUTOMATION_ID_GUARDAR,
                                   UIA_TIPO_BUTTON)
                if boton is None:
                    candidatos = uia.por_nombre(elemento, NOMBRE_BOTON_ACEPTAR,
                                                [UIA_TIPO_BUTTON])
                    boton = candidatos[0] if candidatos else None
            except Exception as exc:                      # noqa: BLE001
                # La ventana pudo cerrarse entre enumerarla y abrirla.
                salida["note"] = f"no se pudo inspeccionar: {type(exc).__name__}"
                salida["dialog_closed"] = not _cuadro_sigue_abierto(ventana["hwnd"])
                salida["waited"] = round(time.monotonic() - inicio, 1)
                return salida
            if boton is None:
                salida["note"] = "el dialogo de plantilla no expone Aceptar"
                salida["waited"] = round(time.monotonic() - inicio, 1)
                return salida
            salida["via"] = uia.invocar(boton)
            salida["accepted"] = True
            salida["dialog_closed"] = _esperar_cierre(ventana["hwnd"], 5.0)
            salida["waited"] = round(time.monotonic() - inicio, 1)
            return salida
        time.sleep(0.3)
    salida["waited"] = round(time.monotonic() - inicio, 1)
    return salida


def guardar_como(peticion: Dict[str, Any]) -> Dict[str, Any]:
    """La secuencia completa. Cada paso comprueba lo que acaba de hacer."""
    pid = int(peticion["desktop_pid"])
    identidad = verificar_proceso(pid, peticion.get("desktop_started"))
    ruta = str(peticion["out_path"])
    extension = str(peticion.get("extension", ".pbix"))
    desde = float(peticion.get("started_at") or time.time())
    pasos: List[Dict[str, Any]] = [{"phase": "identidad", **identidad}]

    uia = Uia()
    principal = _ventana_principal(pid)
    pasos.append({"phase": "ventana", "hwnd": principal["hwnd"],
                  "title": _redactar(principal["title"], 60)})

    if not traer_al_frente(principal["hwnd"], pid):
        raise HelperError("abrir_cuadro",
                          "No se pudo poner Power BI Desktop al frente; no se "
                          "envian teclas que acabarian en otra ventana.",
                          hwnd=principal["hwnd"])
    _enviar_teclas([_tecla(0x7B)])                        # F12
    time.sleep(0.05)
    _enviar_teclas([_tecla(0x7B, arriba=True)])
    pasos.append({"phase": "abrir_cuadro", "accelerator": "F12"})

    cuadro = _esperar_cuadro(uia, pid, float(peticion.get("dialog_timeout", 60)))
    pasos.append({"phase": "cuadro", "hwnd": cuadro["hwnd"]})

    fuera = [cuadro["hwnd"], principal["hwnd"]]
    fase_actual = "tipo"
    try:
        tipo = _elegir_tipo(uia, cuadro["hwnd"], extension)
        pasos.append({"phase": "tipo", **tipo})

        fase_actual = "nombre"
        nombre = _escribir_ruta(uia, cuadro["hwnd"], ruta, pid)
        pasos.append({"phase": "nombre", **nombre})

        fase_actual = "guardar"
        confirmacion = _confirmar_con_verificacion(
            uia, cuadro["hwnd"], pid, ruta,
            plazo=float(peticion.get("save_timeout", 120)), desde=desde,
            overwrite=bool(peticion.get("overwrite")), excluir=fuera)
        pasos.append({"phase": "guardar", **confirmacion})
    except HelperError as exc:
        # El cuadro que abrio ESTA operacion no se deja colgado: se intenta
        # cancelar -solo ese- y se cuenta el resultado, sea cual sea.
        exc.detalles["cleanup"] = _cancelar_cuadro(uia, cuadro["hwnd"], pid)
        exc.detalles["steps"] = pasos
        raise
    except Exception as exc:
        # Un fallo no previsto tampoco deja el cuadro abierto. No se disfraza
        # de nada: viaja con su tipo y su fase, y con la limpieza hecha.
        raise HelperError(
            fase_actual, f"{type(exc).__name__}: {exc}", reason="unexpected",
            cleanup=_cancelar_cuadro(uia, cuadro["hwnd"], pid),
            steps=pasos) from exc

    cerrado = confirmacion["dialog_closed"]
    plantilla = None
    try:
        fase_actual = "plantilla"
        if cerrado and extension.casefold() == ".pbit":
            plantilla = _atender_dialogo_de_plantilla(
                uia, pid, fuera,
                plazo=min(30.0, float(peticion.get("save_timeout", 120))))
            pasos.append({"phase": "plantilla", **plantilla})
        fase_actual = "cierre"
        modales = list(confirmacion.get("blocking_modals") or []) or \
            _modales(uia, pid, fuera)
    except HelperError as exc:
        exc.detalles["steps"] = pasos
        raise
    except Exception as exc:
        # Lo de despues del guardado tambien cuenta: un fallo aqui dejaba la
        # exportacion sin fase y sin evidencia -paso con un puntero COM nulo
        # al mirar el dialogo de plantilla, con el archivo ya escrito-.
        raise HelperError(
            fase_actual, f"{type(exc).__name__}: {exc}",
            reason="unexpected_after_save", steps=pasos,
            dialog_closed=cerrado) from exc
    pasos.append({"phase": "cierre", "dialog_closed": cerrado,
                  "modals": modales})

    return {
        "ok": True,
        "phase": "done",
        "file_type_selected": tipo["file_type_selected"],
        "commit_method": confirmacion["commit_method"],
        "expand_state_after": tipo["expand_state_after"],
        "filename_verified": nombre["filename_verified"],
        "filename_method": nombre.get("method"),
        "filename_verified_by": nombre.get("verified_by"),
        "dialog_closed": cerrado,
        "modals": modales,
        "overwrite_confirmed": confirmacion.get("overwrite_confirmed"),
        "template_dialog": plantilla,
        "steps": pasos,
    }


# ------------------------------------------------- navegar en la ventana ----
def seleccionar_pagina(peticion: Dict[str, Any]) -> Dict[str, Any]:
    """Activa la pestaña de una pagina en una ventana ABIERTA, y lo verifica.

    Evita el ciclo cerrar -> editar `pages.json` -> reabrir -> refrescar que
    costaba cuarenta segundos por pagina. Se busca la pestaña por su nombre
    visible entre los `TabItem` de la ventana principal, se selecciona por el
    patron SelectionItem y se relee `IsSelected`. Si la interfaz no expone
    ese estado, se devuelve `verified=false` con la razon: no se afirma una
    pagina que no se pudo demostrar.
    """
    pid = int(peticion["desktop_pid"])
    identidad = verificar_proceso(pid, peticion.get("desktop_started"))
    nombre = str(peticion.get("page_name") or "").strip()
    if not nombre:
        raise HelperError("pagina", "Falta el nombre visible de la pagina.",
                          reason="page_name_missing")
    pasos: List[Dict[str, Any]] = [{"phase": "identidad", **identidad}]
    uia = Uia()
    principal = _ventana_principal(pid)
    pasos.append({"phase": "ventana", "hwnd": principal["hwnd"]})

    def intento(_numero: int) -> Dict[str, Any]:
        raiz = uia.desde_hwnd(principal["hwnd"])
        pestanas = uia.todos_de_tipo(raiz, UIA_TIPO_TABITEM)
        nombres = [uia.nombre(p) for p in pestanas]
        # Las pestañas de PAGINA y las de la CINTA son el mismo tipo de
        # control: contra Desktop real conviven "Inicio", "Ver", "Ayuda" y
        # "Page 1" en la misma lista. Una pagina llamada como una pestaña de
        # la cinta produce dos candidatas, y ahi no se elige: seleccionar la
        # de la cinta daria `IsSelected` verdadero y una captura de otra
        # pagina con aspecto de exito.
        coincidencias = [p for p in pestanas
                         if uia.nombre(p).strip().casefold() == nombre.casefold()]
        if not coincidencias:
            raise HelperError(
                "pagina", "No hay ninguna pestaña con ese nombre en la "
                "ventana de Power BI Desktop.", transitoria=True,
                reason="page_tab_not_found", page=nombre,
                tabs_seen=[n for n in nombres if n][:40])
        if len(coincidencias) > 1:
            raise HelperError(
                "pagina", f"Hay {len(coincidencias)} pestañas llamadas "
                f"'{nombre}' en la ventana -la cinta usa el mismo tipo de "
                "control que las paginas- y no se elige ninguna.",
                reason="page_tab_ambiguous", page=nombre,
                matches=len(coincidencias),
                tabs_seen=[n for n in nombres if n][:40])
        objetivo = coincidencias[0]
        ya = uia.esta_seleccionado(objetivo)
        via = "already_selected" if ya else uia.seleccionar(objetivo)
        estado = _hasta_que(
            lambda: (True if uia.esta_seleccionado(objetivo) else None),
            plazo=ESPERA_INTERFAZ)
        seleccionado = uia.esta_seleccionado(objetivo)
        return {"page": nombre, "via": via, "verified": bool(estado),
                "selection_state": seleccionado,
                "verification_reason": (
                    None if estado else
                    "la pestaña no expone IsSelected" if seleccionado is None
                    else "la pestaña no quedo seleccionada"),
                "tabs_seen": [n for n in nombres if n][:40]}

    resultado = _con_intentos("pagina", intento)
    pasos.append({"phase": "pagina", **resultado})
    return {"ok": True, "phase": "done", **resultado, "steps": pasos}


def ajustar_a_pagina(peticion: Dict[str, Any]) -> Dict[str, Any]:
    """Pone la vista en "Ajustar a la pagina" desde la cinta, y lo verifica.

    Camino: pestaña Vista -> Vista de pagina -> Ajustar a la pagina. Cada
    control se busca por su nombre accesible (español o ingles). Si la opcion
    no expone un estado que se pueda releer -Toggle o SelectionItem-, se
    devuelve `verified=false` con la razon: el padre decide si le vale.
    """
    pid = int(peticion["desktop_pid"])
    identidad = verificar_proceso(pid, peticion.get("desktop_started"))
    pasos: List[Dict[str, Any]] = [{"phase": "identidad", **identidad}]
    uia = Uia()
    principal = _ventana_principal(pid)
    pasos.append({"phase": "ventana", "hwnd": principal["hwnd"]})

    def _estado(elemento) -> Optional[bool]:
        toggle = uia.estado_toggle(elemento)
        if toggle is not None:
            return toggle == 1
        return uia.esta_seleccionado(elemento)

    def intento(_numero: int) -> Dict[str, Any]:
        raiz = uia.desde_hwnd(principal["hwnd"])
        camino: List[str] = []
        opciones = uia.por_nombre(raiz, NOMBRE_AJUSTAR_A_PAGINA)
        if not opciones:
            vistas = uia.por_nombre(raiz, NOMBRE_PESTANA_VISTA, [UIA_TIPO_TABITEM])
            if vistas:
                uia.seleccionar(vistas[0])
                camino.append("view_tab")
                time.sleep(0.3)
            menus = uia.por_nombre(raiz, NOMBRE_VISTA_DE_PAGINA)
            if menus:
                uia.invocar(menus[0])
                camino.append("page_view_menu")
                opciones = _hasta_que(
                    lambda: (uia.por_nombre(raiz, NOMBRE_AJUSTAR_A_PAGINA)
                             or None), plazo=ESPERA_INTERFAZ) or []
        if not opciones:
            raise HelperError(
                "zoom", "No se encontro el control 'Ajustar a la pagina' en "
                "la cinta de Power BI Desktop.", transitoria=True,
                reason="fit_to_page_control_not_found", path=camino)
        opcion = opciones[0]
        antes = _estado(opcion)
        via = "already_selected" if antes else uia.invocar(opcion)
        camino.append("fit_to_page")
        estado = _hasta_que(lambda: (True if _estado(opcion) else None),
                            plazo=ESPERA_INTERFAZ)
        despues = _estado(opcion)
        return {"via": via, "path": camino, "verified": bool(estado),
                "state_after": despues,
                "verification_reason": (
                    None if estado else
                    "el control no expone Toggle ni SelectionItem"
                    if despues is None else "el control no quedo activado")}

    resultado = _con_intentos("zoom", intento)
    pasos.append({"phase": "zoom", **resultado})
    return {"ok": True, "phase": "done", **resultado, "steps": pasos}


ACCIONES = {"save_as": guardar_como,
            "select_page": seleccionar_pagina,
            "fit_to_page": ajustar_a_pagina}


def main() -> int:
    try:
        peticion = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        sys.stdout.write(json.dumps(
            {"ok": False, "phase": "peticion",
             "error": f"peticion ilegible: {type(exc).__name__}"}))
        return 2

    accion = ACCIONES.get(str(peticion.get("action", "")))
    if accion is None:
        sys.stdout.write(json.dumps(
            {"ok": False, "phase": "peticion",
             "error": f"accion desconocida: {peticion.get('action')!r}",
             "valid": sorted(ACCIONES)}))
        return 2

    try:
        respuesta = accion(peticion)
    except HelperError as exc:
        respuesta = {"ok": False, "phase": exc.fase,
                     "error": _redactar(exc.mensaje),
                     "transient": exc.transitoria,
                     "details": {k: _redactar(v) if isinstance(v, str) else v
                                 for k, v in exc.detalles.items()}}
    except Exception as exc:                              # noqa: BLE001
        respuesta = {"ok": False, "phase": "inesperado",
                     "error": f"{type(exc).__name__}: {_redactar(exc)}"}
    # stdout es SOLO esto: el padre lo parsea entero.
    sys.stdout.write(json.dumps(respuesta, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return 0 if respuesta.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
