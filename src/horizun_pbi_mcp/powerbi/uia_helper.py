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
"""
from __future__ import annotations

import functools
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------- constantes --
CLASE_DIALOGO = "#32770"
AUTOMATION_ID_NOMBRE = "FileNameControlHost"
AUTOMATION_ID_TIPO = "FileTypeControlHost"
AUTOMATION_ID_GUARDAR = "1"

UIA_PROP_CONTROLTYPE = 30003
UIA_PROP_AUTOID = 30011
UIA_TIPO_COMBOBOX = 50003
UIA_TIPO_LISTITEM = 50007
UIA_TIPO_BUTTON = 50000
UIA_PAT_INVOCAR = 10000
UIA_PAT_VALOR = 10002
UIA_PAT_EXPANDIR = 10005
UIA_PAT_SELITEM = 10010
UIA_PAT_LEGACY = 10018
UIA_SCOPE_DESC = 4
#: ExpandCollapseState: 0 = cerrado. Cerrado tras activar = hubo compromiso.
ESTADO_CERRADO = 0

VK_CONTROL, VK_A, VK_RETURN = 0x11, 0x41, 0x0D
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000


class HelperError(Exception):
    """Fallo con fase y diagnostico, para que el padre sepa donde paro."""

    def __init__(self, fase: str, mensaje: str, **detalles: Any):
        super().__init__(mensaje)
        self.fase = fase
        self.mensaje = mensaje
        self.detalles = detalles


def _redactar(valor: Any, maximo: int = 300) -> str:
    """Quita el directorio personal. El helper no exporta rutas de nadie."""
    import re

    texto = str(valor)
    casa = os.path.expanduser("~")
    for variante in {casa, casa.replace("\\", "/"), casa.replace("/", "\\")}:
        if variante and len(variante) > 3:
            texto = re.sub(re.escape(variante), "~", texto, flags=re.IGNORECASE)
    return texto[:maximo]


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
    import ctypes
    from ctypes import wintypes

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
    import ctypes
    from ctypes import wintypes

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
            "bloqueada o que otro proceso tiene tomada la entrada.")


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


def escribir_texto_real(texto: str) -> None:
    """Teclea el texto como lo hace una persona, caracter a caracter.

    Se usa `KEYEVENTF_UNICODE` en vez de codigos de tecla: asi el resultado no
    depende de la distribucion del teclado, que es justo lo que rompe un
    automatismo cuando cambia de maquina.
    """
    eventos: List[Any] = []
    for letra in texto:
        eventos.append(_caracter(letra))
        eventos.append(_caracter(letra, arriba=True))
    for i in range(0, len(eventos), 40):        # tandas: SendInput tiene tope
        _enviar_teclas(eventos[i:i + 40])
        time.sleep(0.01)


def seleccionar_todo() -> None:
    _enviar_teclas([_tecla(VK_CONTROL), _tecla(VK_A)])
    time.sleep(0.05)
    _enviar_teclas([_tecla(VK_A, arriba=True), _tecla(VK_CONTROL, arriba=True)])
    time.sleep(0.1)


def _rect_ventana(hwnd: int):
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect


def clic_dinamico(punto, dialogo_hwnd: int, pid_esperado: int) -> Dict[str, Any]:
    """Un clic real en un punto CALCULADO del elemento, nunca memorizado.

    Se exige, en este orden: que el punto caiga dentro del rectangulo del
    cuadro de guardado, y que la ventana que hay en ese punto pertenezca al
    proceso verificado. Si algo no cuadra, no se pulsa: un clic en las
    coordenadas equivocadas cae en la aplicacion de otra persona.
    """
    import ctypes
    from ctypes import wintypes

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
        condicion = self.auto.CreateAndCondition(
            self.auto.CreatePropertyCondition(UIA_PROP_AUTOID, automation_id),
            self.auto.CreatePropertyCondition(UIA_PROP_CONTROLTYPE, tipo))
        return raiz.FindFirst(UIA_SCOPE_DESC, condicion)

    def valor(self, elemento) -> Optional[str]:
        try:
            return elemento.GetCurrentPattern(UIA_PAT_VALOR).QueryInterface(
                self.modulo.IUIAutomationValuePattern).CurrentValue
        except Exception:                                 # noqa: BLE001
            return None

    def expandir(self, combo):
        combo.GetCurrentPattern(UIA_PAT_EXPANDIR).QueryInterface(
            self.modulo.IUIAutomationExpandCollapsePattern).Expand()

    def estado_expandido(self, combo) -> Optional[int]:
        try:
            return int(combo.GetCurrentPattern(UIA_PAT_EXPANDIR).QueryInterface(
                self.modulo.IUIAutomationExpandCollapsePattern
            ).CurrentExpandCollapseState)
        except Exception:                                 # noqa: BLE001
            return None

    def items(self, combo) -> List[Any]:
        condicion = self.auto.CreatePropertyCondition(
            UIA_PROP_CONTROLTYPE, UIA_TIPO_LISTITEM)
        encontrados = combo.FindAll(UIA_SCOPE_DESC, condicion)
        return [encontrados.GetElement(i) for i in range(encontrados.Length)]

    def invocar(self, elemento) -> str:
        """Invoke -> DoDefaultAction. Devuelve por cual salio."""
        try:
            elemento.GetCurrentPattern(UIA_PAT_INVOCAR).QueryInterface(
                self.modulo.IUIAutomationInvokePattern).Invoke()
            return "invoke"
        except Exception:                                 # noqa: BLE001
            elemento.GetCurrentPattern(UIA_PAT_LEGACY).QueryInterface(
                self.modulo.IUIAutomationLegacyIAccessiblePattern
            ).DoDefaultAction()
            return "legacy_default_action"

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
    import ctypes
    from ctypes import wintypes

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
        salida.append({"hwnd": int(hwnd), "class": clase.value,
                       "title": titulo.value})
        return True

    user32.EnumWindows(visita, 0)
    return salida


def traer_al_frente(hwnd: int, pid: int) -> bool:
    """Primer plano por la via documentada, y COMPROBADO despues.

    `SetForegroundWindow` a secas lo ignora Windows cuando quien llama no es
    el duenio del foco -un servidor lanzado desde consola nunca lo es-, y no
    falla ruidosamente: solo parpadea el boton de la barra de tareas.
    """
    import ctypes
    from ctypes import wintypes

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


def _elegir_tipo(uia: Uia, dialogo_hwnd: int, extension: str) -> Dict[str, Any]:
    """El tipo se ELIGE y se comprueba que la aplicacion lo proceso.

    `Select()` cambia lo que se ve y deja la lista abierta; `Invoke()` ejecuta
    la accion por defecto del elemento y la lista se cierra sola. Que se
    cierre es la señal de que hubo compromiso, no solo repintado.
    """
    elemento = uia.desde_hwnd(dialogo_hwnd)
    combo = uia.por_id(elemento, AUTOMATION_ID_TIPO, UIA_TIPO_COMBOBOX)
    if combo is None:
        raise HelperError("tipo", "El cuadro no expone el desplegable de tipo.",
                          automation_id=AUTOMATION_ID_TIPO)
    previo = uia.valor(combo)
    uia.expandir(combo)
    time.sleep(0.8)
    opciones = uia.items(combo)
    nombres = [o.CurrentName for o in opciones]
    objetivo = extension.casefold().lstrip("*")
    elegido = next((o for o in opciones
                    if objetivo in (o.CurrentName or "").casefold()), None)
    if elegido is None:
        raise HelperError("tipo", f"El cuadro no ofrece '{extension}'.",
                          available=nombres, current=previo)
    nombre = elegido.CurrentName
    via = uia.invocar(elegido)
    time.sleep(0.8)

    estado = uia.estado_expandido(combo)
    if estado not in (ESTADO_CERRADO, None):
        raise HelperError(
            "tipo", "El desplegable sigue abierto tras activar la opcion: la "
            "aplicacion no proceso la eleccion.",
            expand_collapse_state=estado, via=via, requested=extension)
    actual = uia.valor(combo)
    if actual and objetivo not in actual.casefold():
        raise HelperError("tipo", "El tipo no quedo en lo pedido.",
                          current=actual, requested=extension)
    return {"file_type_selected": nombre, "via": via, "previous": previo,
            "expand_state_after": estado, "available": nombres}


def _escribir_ruta(uia: Uia, dialogo_hwnd: int, ruta: str) -> Dict[str, Any]:
    """Ruta ABSOLUTA, tecleada como una persona, y releida para comprobar."""
    elemento = uia.desde_hwnd(dialogo_hwnd)
    campo = uia.por_id(elemento, AUTOMATION_ID_NOMBRE, UIA_TIPO_COMBOBOX)
    if campo is None:
        raise HelperError("nombre", "El cuadro no expone el campo del nombre.",
                          automation_id=AUTOMATION_ID_NOMBRE)
    uia.enfocar(campo)
    time.sleep(0.3)
    seleccionar_todo()
    escribir_texto_real(ruta)
    time.sleep(0.4)

    escrito = (uia.valor(campo) or "").strip('"')
    if escrito != ruta:
        raise HelperError(
            "nombre", "El campo del nombre no quedo con la ruta pedida.",
            expected_len=len(ruta), actual_len=len(escrito))
    return {"filename_verified": True, "length": len(ruta)}


def _confirmar(uia: Uia, dialogo_hwnd: int, pid: int) -> Dict[str, Any]:
    """Invoke -> DoDefaultAction -> clic real calculado. En ese orden.

    Los dos primeros no reproducen el clic humano en este cuadro -se
    comprobo: el archivo no aparece-, asi que existe el tercero. El punto se
    calcula del propio elemento en el momento; no hay coordenadas escritas ni
    supuestos sobre DPI, monitor o escala.
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

    # ¿Se cerro el cuadro? Si si, el patron basto y no se toca el raton.
    time.sleep(1.2)
    if not _cuadro_sigue_abierto(dialogo_hwnd):
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
    from ctypes import wintypes

    user32 = _user32()
    user32.IsWindow.argtypes = [wintypes.HWND]
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def _esperar_cierre(hwnd: int, plazo: float) -> bool:
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if not _cuadro_sigue_abierto(hwnd):
            return True
        time.sleep(0.3)
    return False


def _modales(uia: Uia, pid: int, excluir: List[int]) -> List[Dict[str, Any]]:
    fuera = set(excluir)
    salida = []
    for ventana in ventanas_de(pid):
        if ventana["class"].casefold() != CLASE_DIALOGO.casefold():
            continue
        if ventana["hwnd"] in fuera:
            continue
        elemento = uia.desde_hwnd(ventana["hwnd"])
        if uia.por_id(elemento, AUTOMATION_ID_TIPO, UIA_TIPO_COMBOBOX):
            continue                        # es otro cuadro de guardado
        salida.append({"hwnd": ventana["hwnd"],
                       "title": _redactar(ventana["title"], 120)})
    return salida


def guardar_como(peticion: Dict[str, Any]) -> Dict[str, Any]:
    """La secuencia completa. Cada paso comprueba lo que acaba de hacer."""
    pid = int(peticion["desktop_pid"])
    identidad = verificar_proceso(pid, peticion.get("desktop_started"))
    ruta = str(peticion["out_path"])
    extension = str(peticion.get("extension", ".pbix"))
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

    tipo = _elegir_tipo(uia, cuadro["hwnd"], extension)
    pasos.append({"phase": "tipo", **tipo})

    nombre = _escribir_ruta(uia, cuadro["hwnd"], ruta)
    pasos.append({"phase": "nombre", **nombre})

    confirmacion = _confirmar(uia, cuadro["hwnd"], pid)
    pasos.append({"phase": "guardar", **confirmacion})

    cerrado = _esperar_cierre(cuadro["hwnd"],
                              float(peticion.get("save_timeout", 120)))
    modales = _modales(uia, pid, [cuadro["hwnd"]])
    pasos.append({"phase": "cierre", "dialog_closed": cerrado,
                  "modals": modales})

    return {
        "ok": True,
        "phase": "done",
        "file_type_selected": tipo["file_type_selected"],
        "commit_method": confirmacion["commit_method"],
        "expand_state_after": tipo["expand_state_after"],
        "filename_verified": nombre["filename_verified"],
        "dialog_closed": cerrado,
        "modals": modales,
        "steps": pasos,
    }


ACCIONES = {"save_as": guardar_como}


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
