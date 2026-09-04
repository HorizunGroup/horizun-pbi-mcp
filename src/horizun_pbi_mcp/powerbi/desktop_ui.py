"""Conducir la ventana de Power BI Desktop, y solo la correcta.

Por que existe
--------------
Microsoft no publica ninguna API para convertir un `.pbip` en `.pbix`. Lo que
si existe es el flujo **oficialmente soportado**: `Archivo > Guardar como` en
Power BI Desktop. Fabricar el `.pbix` a mano -zip, TOM, ingenieria inversa-
produce archivos que Desktop abre a veces y rompe otras; conducir la interfaz
usa el mismo codigo que usaria una persona.

Lo que este modulo NO hace, a proposito
---------------------------------------
- **No hace clic por coordenadas.** Cada control se resuelve por su
  identificador de dialogo y su clase de ventana. Un clic en (412, 588)
  depende del DPI, del monitor, del idioma y de si la ventana esta maximizada;
  el identificador `cmb1` no depende de nada de eso.
- **No usa "la ultima ventana" ni "el proceso mas reciente".** Toda operacion
  arranca de un `pid` cuya identidad ya se verifico (nombre del proceso y hora
  de arranque), y las ventanas se enumeran filtrando por ese pid exacto.
- **No envia teclas al aire.** Antes de mandar el acelerador se comprueba que
  la ventana en primer plano PERTENECE a ese pid. Sin esa comprobacion,
  `Ctrl+Shift+S` puede acabar en el editor de otra persona.
- **No da por hecho el tipo de archivo.** El desplegable de tipos se LEE y se
  elige la entrada que corresponde a `.pbix`. Power BI Desktop ofrece tambien
  `.pbit` -plantilla, sin datos- y aceptar el valor por defecto es como se
  entrega una plantilla vacia creyendo que es el informe.

La frontera
-----------
Todo lo que toca Windows vive detras de `AdaptadorUI`. La suite usa un doble;
el adaptador real solo se ejerce en una prueba `live`, porque necesita una
sesion grafica y Power BI Desktop instalado. Esa separacion no es cosmetica:
es lo que permite probar la LOGICA -que se verifica la identidad, que se
fuerza el tipo, que un modal no se confunde con un timeout- sin una pantalla.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("desktop_ui")

#: Clase de los dialogos comunes de Windows (Guardar como, mensajes).
CLASE_DIALOGO = "#32770"

#: Identificadores del cuadro de guardado MODERNO (Common Item Dialog), los
#: que de verdad tiene Power BI Desktop. No son los del dialogo clasico de
#: `commdlg`: se comprobaron enumerando la ventana real, porque
#: `GetDlgItem(dlg, cmb1)` devuelve NULL -los controles cuelgan de contenedores
#: `FloatNotifySink` y casi todos llevan identificador 0-.
#:
#: Lo que SI identifica cada control es la combinacion de clase, identificador
#: y jerarquia, y eso es lo que se usa:
#:
#:   nombre de archivo -> Edit id 1001 cuyo padre es un ComboBox
#:   tipo de archivo   -> ComboBox con elementos y SIN Edit hijo
#:   guardar           -> Button id 1 (IDOK), hijo directo del dialogo
IDC_NOMBRE_EDIT = 0x03E9         # 1001
IDOK = 1
IDCANCEL = 2
#: Texto estatico de un cuadro de mensaje.
IDC_TEXTO_MENSAJE = 0xFFFF

# Mensajes Win32 que se usan. Se declaran aqui para que se lea que hace cada
# llamada sin tener que buscar el numero.
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
CB_GETCOUNT = 0x0146
CB_SETCURSEL = 0x014E
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149


class DesktopUIError(PowerBIMCPError):
    code = "desktop_ui_error"


class DesktopUINotAvailable(DesktopUIError):
    """No hay forma de conducir la interfaz en este entorno."""

    code = "desktop_ui_unavailable"


class DesktopModalError(DesktopUIError):
    """Hay un dialogo esperando a una persona. NO es un timeout."""

    code = "desktop_modal"


@dataclass(frozen=True)
class Ventana:
    hwnd: int
    pid: int
    title: str
    class_name: str


#: Clases de modal que se reconocen, con la accion que se sugiere. El texto
#: accesible se redacta antes de devolverlo: un dialogo de credenciales puede
#: llevar un nombre de usuario o el nombre del servidor del cliente.
CLASES_DE_MODAL: Sequence[tuple[str, re.Pattern, str]] = (
    ("credentials",
     re.compile(r"credencial|credential|iniciar sesi|sign in|autentic|"
                r"usuario y contrase|username|password", re.I),
     "Power BI Desktop pide credenciales del origen. Autenticate en la "
     "ventana y repite: las credenciales viven en Desktop, no en el .pbip."),
    ("data_load_error",
     re.compile(r"no se pudo cargar|couldn't load|failed to load|"
                r"error de or[ií]gen|datasource error|expression\.error", re.I),
     "El modelo no pudo cargar los datos. Revisa el origen antes de exportar; "
     "un .pbix guardado ahora saldria sin datos."),
    ("unsaved_changes",
     re.compile(r"guardar los cambios|save (your )?changes|cambios sin guardar|"
                r"unsaved", re.I),
     "Hay cambios sin guardar. Decide tu que hacer con ellos: cerrar este "
     "dialogo automaticamente podria perder trabajo."),
    ("confirm_replace",
     re.compile(r"ya existe|already exists|confirmar.*reemplaz|"
                r"confirm save as|replace it", re.I),
     "El destino ya existe y Windows pide confirmacion. Vuelve a llamar con "
     "overwrite=true si de verdad quieres reemplazarlo."),
    ("path_too_long",
     re.compile(r"demasiado larg|too long|path.*260|nombre de archivo.*largo",
                re.I),
     "La ruta de destino supera el limite de Windows. Elige una carpeta con "
     "una ruta mas corta."),
    ("file_locked",
     re.compile(r"en uso|in use|bloquead|locked|otro programa|another program",
                re.I),
     "El archivo de destino esta abierto en otro programa. Cierralo y repite."),
    ("wrong_format",
     re.compile(r"formato|format|no v[aá]lido|not valid|extension", re.I),
     "Windows no acepto el nombre o el formato indicado. Comprueba que la "
     "ruta termina en .pbix."),
    ("save_failed",
     re.compile(r"no se pudo guardar|couldn'?t save|failed to save|"
                r"error al guardar", re.I),
     "Power BI Desktop no pudo guardar. El destino no es utilizable; revisa "
     "permisos y espacio en disco."),
)


def clasificar_modal(titulo: str, texto: str) -> tuple[str, str]:
    """(clase, accion sugerida) de un dialogo, por su texto accesible."""
    completo = f"{titulo}\n{texto}"
    for clase, patron, accion in CLASES_DE_MODAL:
        if patron.search(completo):
            return clase, accion
    return ("unknown",
            "Hay un dialogo abierto en Power BI Desktop esperando una "
            "respuesta. Atiendelo en la ventana y repite la operacion.")


def _nombre_de_proceso(pid: int) -> Optional[str]:
    """Nombre del ejecutable de un pid. Solo el nombre: ni ruta ni argumentos."""
    if not pid:
        return None
    try:
        import psutil

        return psutil.Process(int(pid)).name()
    except Exception:                                     # noqa: BLE001
        return None


def redactar(texto: str, *, maximo: int = 300) -> str:
    """Texto de un dialogo, sin rutas personales ni credenciales."""
    from horizun_pbi_mcp.services import redaction

    limpio = redaction.texto(" ".join(str(texto or "").split()))
    return limpio[:maximo] + ("..." if len(limpio) > maximo else "")


@dataclass
class Modal:
    hwnd: int
    title: str
    text: str
    kind: str
    suggested_action: str

    def to_dict(self) -> Dict[str, Any]:
        return {"hwnd": self.hwnd, "title": self.title, "text": self.text,
                "kind": self.kind, "suggested_action": self.suggested_action}


#: Tope para conducir el cuadro de guardado. El plazo global de la exportacion
#: -que incluye abrir Desktop y refrescar el modelo- puede ser de minutos, pero
#: mover un desplegable y pulsar un boton no. Darle al helper el presupuesto
#: entero convertia un fallo de dos segundos en una espera de un cuarto de hora.
#: Por que los tres pasos granulares del adaptador real se niegan a correr.
#: Se midieron contra Power BI Desktop: `CB_SETCURSEL` cambia lo que se LEE en
#: el desplegable sin avisar a la aplicacion, y Desktop siguio guardando con el
#: filtro anterior -se pedia .pbix y salia un proyecto .pbip-; `BM_CLICK` sobre
#: Guardar cierra el cuadro SIN escribir nada. Conservar el codigo "por si
#: acaso" era peor que no tenerlo: se leia como si funcionara, y fue justo lo
#: que hizo que el arreglo de verdad tardara tres intentos en encontrarse.
_POR_QUE_NO_WIN32 = (
    "Los mensajes Win32 no sirven para {paso} en este cuadro: cambian lo que "
    "se ve sin avisar a Power BI Desktop, que sigue guardando con el formato "
    "anterior. Se comprobo contra la aplicacion real. Usa `save_as_completo`, "
    "que conduce el cuadro por UI Automation desde un proceso aparte."
)

LIMITE_HELPER = 120.0

#: COM ya no se toca en ESTE proceso. Conducir el cuadro es cosa de
#: `uia_helper`, que corre aparte: una llamada COM bloqueada no se puede
#: cancelar desde dentro -el intento anterior la dejaba colgada en un hilo
#: demonio y llamaba "timeout" a haber dejado de mirarla-, y ademas importar
#: comtypes aqui fijaba el apartamento del hilo del servidor y rompia
#: pythonnet con «Cannot change thread mode after it is set».


class AdaptadorUI(Protocol):
    """Lo minimo que hace falta para conducir un `Guardar como`.

    Los cuerpos son SOLO su docstring, no `...`. Es equivalente -ambos dejan
    la funcion devolviendo None- y ademas dice para que sirve cada uno, que es
    lo que un Protocol deberia aportar. Un doble de pruebas implementa esto
    entero sin abrir ninguna ventana; el adaptador real vive mas abajo.
    """

    def ventana_principal(self, pid: int,
                          started: Optional[float]) -> Ventana:
        """La ventana del documento de ESE proceso, con identidad verificada."""

    def enfocar(self, ventana: Ventana) -> bool:
        """La pone en primer plano. False si Windows no cedio el foco."""

    def abrir_guardar_como(self, ventana: Ventana) -> None:
        """Manda el acelerador, y solo si esa ventana esta al frente."""

    def esperar_dialogo_guardado(self, pid: int, *,
                                 timeout: float) -> Ventana:
        """Espera al cuadro, reconocido por sus controles y no por su titulo."""

    def tipos_de_archivo(self, dialogo: Ventana) -> List[str]:
        """Lo que el desplegable OFRECE, para no aceptar el tipo por defecto."""

    def elegir_tipo(self, dialogo: Ventana, extension: str) -> str:
        """Deja el tipo en `extension` y devuelve la entrada elegida."""

    def escribir_ruta(self, dialogo: Ventana, ruta: str) -> None:
        """Pone la ruta ABSOLUTA en el campo del nombre y la relee."""

    def confirmar(self, dialogo: Ventana) -> None:
        """Pulsa Guardar."""

    def save_as_completo(self, *, pid: int, started: Optional[float],
                         destino: str, extension: str = ".pbix",
                         timeout: float = 180.0) -> Dict[str, Any]:
        """Todo el guardado de una vez, con evidencia de cada paso.

        Es el camino real: el adaptador de produccion lo hace desde un proceso
        aparte. Un adaptador que no lo ofrezca hace que el servicio recorra
        los pasos de arriba uno a uno.
        """

    def esperar_cierre(self, dialogo: Ventana, *, timeout: float) -> bool:
        """Si el cuadro se cerro dentro del plazo. Cerrarse no es haber escrito."""

    def modales(self, pid: int, *,
                excluir: Sequence[int] = ()) -> List[Modal]:
        """Dialogos abiertos de ese proceso, clasificados y redactados."""

    def seleccionar_pagina(self, *, pid: int, started: Optional[float],
                           page_name: str,
                           timeout: float = 30.0) -> Dict[str, Any]:
        """Activa la pestaña de esa pagina en la ventana abierta y lo verifica."""

    def ajustar_a_pagina(self, *, pid: int, started: Optional[float],
                         timeout: float = 30.0) -> Dict[str, Any]:
        """Pone la vista en 'Ajustar a la pagina' y dice si pudo comprobarlo."""


#: Tope para una accion de navegacion (elegir pestaña, cambiar el zoom). Es
#: un par de clics; si tarda mas, la interfaz no esta respondiendo.
LIMITE_NAVEGACION = 30.0


# ------------------------------------------------------------------ Win32 ----
def _user32():
    import ctypes

    if os.name != "nt":
        raise DesktopUINotAvailable(
            "Conducir Power BI Desktop solo es posible en Windows.",
            details={"platform": os.name})
    return ctypes.WinDLL("user32", use_last_error=True)


class Win32UIAdapter:
    """Adaptador real. Cada control se resuelve por identificador, no por sitio.

    Se apoya en la enumeracion de ventanas que ya existia para las capturas
    (`desktop_capture._enumerate_windows`), que filtra por `pid` exacto. No se
    anade ninguna dependencia: los controles del dialogo comun de archivo son
    ventanas Win32 con identificadores documentados, y eso es exactamente lo
    que hace falta para operarlos sin tocar la pantalla.
    """

    def __init__(self):
        self._ventana_para_reintento: Optional[Ventana] = None

    #: Aceleradores de "Guardar como", en orden de preferencia. **F12 es el
    #: que funciona**: se comprobo contra Power BI Desktop real, donde
    #: `Ctrl+Shift+S` no abre nada. Se conserva como segundo intento porque la
    #: combinacion depende de la version y del idioma, y probar el segundo
    #: cuesta cuatro segundos.
    ACELERADORES_GUARDAR_COMO = (("f12",), ("ctrl", "shift", "s"))

    def _ventanas(self, pid: int) -> List[Ventana]:
        from horizun_pbi_mcp.powerbi.desktop_capture import _enumerate_windows

        try:
            crudas = _enumerate_windows(int(pid))
        except Exception as exc:                          # noqa: BLE001
            raise DesktopUIError(
                f"No se pudieron enumerar las ventanas del proceso {pid}.",
                details={"pid": pid, "cause": type(exc).__name__}) from exc
        return [Ventana(hwnd=w.hwnd, pid=w.pid, title=w.title,
                        class_name=w.class_name) for w in crudas]

    def ventana_principal(self, pid: int,
                          started: Optional[float]) -> Ventana:
        from horizun_pbi_mcp.powerbi.desktop_capture import (
            _assert_desktop_identity)

        # La identidad primero: sin esto, un PID reciclado nos pondria a
        # teclear en el proceso de otro.
        _assert_desktop_identity(int(pid), started)
        ventanas = [v for v in self._ventanas(pid) if v.title.strip()]
        if not ventanas:
            raise DesktopUIError(
                "Power BI Desktop no tiene ninguna ventana visible con titulo.",
                details={"pid": pid, "reason": "desktop_window_not_ready"})
        principales = [v for v in ventanas
                       if v.class_name.casefold() != CLASE_DIALOGO.casefold()]
        if len(principales) == 1:
            return principales[0]
        if not principales:
            raise DesktopUIError(
                "Solo hay dialogos abiertos: la ventana principal de Power BI "
                "Desktop no esta disponible todavia.",
                details={"pid": pid, "reason": "only_dialogs_visible"})
        raise DesktopUIError(
            "Ese proceso tiene varias ventanas principales y ninguna se puede "
            "señalar sin adivinar.",
            details={"pid": pid, "reason": "desktop_window_ambiguous",
                     "windows": [{"hwnd": v.hwnd, "title": v.title}
                                 for v in principales]})

    def _duenio_del_primer_plano(self) -> tuple:
        """(hwnd, pid, hilo) de la ventana que tiene el foco ahora mismo."""
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        frente = user32.GetForegroundWindow()
        duenio = wintypes.DWORD()
        hilo = user32.GetWindowThreadProcessId(frente, ctypes.byref(duenio))
        return int(frente or 0), int(duenio.value), int(hilo)

    def enfocar(self, ventana: Ventana) -> bool:
        """Trae la ventana al frente y COMPRUEBA que se quedo ahi.

        `SetForegroundWindow` a secas NO basta y no falla ruidosamente: cuando
        el proceso que llama no es el duenio del primer plano, Windows lo
        ignora y se limita a parpadear el boton de la barra de tareas. Un
        servidor MCP corre desde una consola, asi que **nunca** tiene ese
        derecho: la primera ejecucion real contra Power BI Desktop se quedo
        justo aqui, con el foco en un navegador.

        La via documentada para pedirlo bien es adjuntar la cola de entrada de
        nuestro hilo a la del hilo que tiene el foco (`AttachThreadInput`):
        mientras estan unidas, Windows nos concede el mismo derecho que a el.
        Se desengancha siempre, tambien si algo falla en medio; dejar dos colas
        de entrada unidas afecta al resto del escritorio.

        Nada de esto mueve el raton ni depende de donde este la ventana.
        """
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                             wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]

        objetivo = wintypes.HWND(ventana.hwnd)
        propio = int(kernel32.GetCurrentThreadId())

        for _ in range(20):
            _frente, duenio, hilo_frente = self._duenio_del_primer_plano()
            if duenio == int(ventana.pid):
                return True

            if user32.IsIconic(objetivo):
                user32.ShowWindow(objetivo, 9)                 # SW_RESTORE
            adjuntado = False
            try:
                if hilo_frente and hilo_frente != propio:
                    adjuntado = bool(user32.AttachThreadInput(
                        wintypes.DWORD(propio), wintypes.DWORD(hilo_frente),
                        True))
                # ASFW_ANY: renuncia a nuestro propio bloqueo de primer plano.
                user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))
                user32.BringWindowToTop(objetivo)
                user32.SetForegroundWindow(objetivo)
            finally:
                if adjuntado:
                    user32.AttachThreadInput(
                        wintypes.DWORD(propio), wintypes.DWORD(hilo_frente),
                        False)

            _frente, duenio, _hilo = self._duenio_del_primer_plano()
            if duenio == int(ventana.pid):
                return True
            time.sleep(0.15)
        return False

    def abrir_guardar_como(self, ventana: Ventana) -> None:
        """Manda el acelerador SOLO si la ventana de ese pid esta al frente."""
        if not self.enfocar(ventana):
            hwnd_frente, pid_frente, hilo = self._duenio_del_primer_plano()
            raise DesktopUIError(
                "No se pudo poner al frente la ventana de Power BI Desktop, "
                "asi que no se envia ninguna tecla: acabaria en la ventana de "
                "otro programa. Suele pasar cuando la sesion esta bloqueada, "
                "minimizada a una sesion RDP desconectada, o cuando otra "
                "aplicacion mantiene un bloqueo de primer plano.",
                details={
                    "reason": "foreground_not_owned",
                    "phase": "abrir_guardar_como/enfocar",
                    "target_hwnd": ventana.hwnd,
                    "target_pid": ventana.pid,
                    "target_class": ventana.class_name,
                    "target_title": redactar(ventana.title, maximo=120),
                    "foreground_hwnd": hwnd_frente,
                    "foreground_pid": pid_frente,
                    "foreground_thread": hilo,
                    "foreground_process": _nombre_de_proceso(pid_frente),
                })
        # Solo el PRIMER acelerador: `esperar_dialogo_guardado` se encarga de
        # probar el siguiente si este no abre nada. Mandar los dos seguidos
        # dejaria dos cuadros de guardado encima del otro.
        self._enviar_acelerador(self.ACELERADORES_GUARDAR_COMO[0])
        self._ventana_para_reintento = ventana

    def _enviar_acelerador(self, teclas: Sequence[str]) -> None:
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        codigos = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12,
                   "s": 0x53, "f12": 0x7B}
        user32 = _user32()
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                       wintypes.DWORD,
                                       ctypes.POINTER(wintypes.ULONG)]
        pulsadas = [codigos[t] for t in teclas]
        for vk in pulsadas:
            user32.keybd_event(vk, 0, 0, None)
        time.sleep(0.05)
        for vk in reversed(pulsadas):
            user32.keybd_event(vk, 0, 2, None)             # KEYEVENTF_KEYUP

    def esperar_dialogo_guardado(self, pid: int, *,
                                 timeout: float) -> Ventana:
        """El dialogo se reconoce por sus CONTROLES, no por su titulo.

        El titulo cambia con el idioma de Windows y con la version; la
        estructura -un campo de nombre y un desplegable de tipo- no. Ademas
        asi no se confunde con un cuadro de mensaje, que tambien es `#32770`.

        Si el primer acelerador no abrio nada, se prueba el siguiente antes de
        rendirse: la combinacion depende de la version de Desktop.
        """
        limite = time.monotonic() + float(timeout)
        visto: List[Dict[str, Any]] = []
        pendientes = list(self.ACELERADORES_GUARDAR_COMO[1:])
        proximo_reintento = time.monotonic() + 8.0

        while time.monotonic() < limite:
            for ventana in self._ventanas(pid):
                if ventana.class_name.casefold() != CLASE_DIALOGO.casefold():
                    continue
                if self._es_cuadro_de_guardado(ventana.hwnd):
                    return ventana
                if not any(v["hwnd"] == ventana.hwnd for v in visto):
                    visto.append({"hwnd": ventana.hwnd,
                                  "title": redactar(ventana.title, maximo=120)})
            modales = self.modales(pid)
            if modales:
                raise DesktopModalError(
                    "Power BI Desktop abrio un dialogo en vez del cuadro de "
                    "guardado.",
                    details={"modals": [m.to_dict() for m in modales]})

            ventana_origen = getattr(self, "_ventana_para_reintento", None)
            if (pendientes and ventana_origen is not None
                    and time.monotonic() >= proximo_reintento):
                combinacion = pendientes.pop(0)
                log.info("El cuadro de guardado no aparecio; se prueba %s",
                         "+".join(combinacion))
                if self.enfocar(ventana_origen):
                    self._enviar_acelerador(combinacion)
                proximo_reintento = time.monotonic() + 8.0
            time.sleep(0.3)

        raise DesktopUIError(
            "No aparecio el cuadro de 'Guardar como' en el plazo indicado.",
            details={"pid": pid, "timeout": timeout,
                     "reason": "save_dialog_not_found",
                     "phase": "esperar_dialogo_guardado",
                     "accelerators_tried": [
                         "+".join(c) for c in self.ACELERADORES_GUARDAR_COMO],
                     "dialogs_seen": visto})

    # -- resolucion de controles: clase + identificador + jerarquia ----------
    def _descendientes(self, hwnd: int) -> List[Dict[str, Any]]:
        """Todo el arbol de controles, con su clase, id y padre.

        `GetDlgItem` no sirve en el cuadro moderno: los controles cuelgan de
        contenedores intermedios y casi todos llevan identificador 0. Lo que
        los distingue es DONDE estan y de que clase son.
        """
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)
        user32.EnumChildWindows.argtypes = [wintypes.HWND, callback,
                                            wintypes.LPARAM]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                         ctypes.c_int]
        user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        salida: List[Dict[str, Any]] = []

        @callback
        def visita(handle, _lparam):
            clase = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, clase, 256)
            salida.append({"hwnd": int(handle), "class": clase.value,
                           "id": int(user32.GetDlgCtrlID(handle)),
                           "parent": int(user32.GetParent(handle) or 0)})
            return True

        user32.EnumChildWindows(wintypes.HWND(hwnd), visita, 0)
        return salida

    def _edicion_de_nombre_en(self, arbol: List[Dict[str, Any]]
                              ) -> Optional[int]:
        """`Edit` id 1001 colgando de un `ComboBox`.

        El identificador solo no basta: la barra de direcciones tiene otro
        `Edit` dentro de otro `ComboBox`, con id 0xA205. La pareja
        (id 1001 + padre ComboBox) es unica en el cuadro.
        """
        combos = {c["hwnd"] for c in arbol if c["class"] == "ComboBox"}
        for control in arbol:
            if (control["class"] == "Edit"
                    and control["id"] == IDC_NOMBRE_EDIT
                    and control["parent"] in combos):
                return control["hwnd"]
        return None

    def _combo_de_tipo_en(self, arbol: List[Dict[str, Any]]) -> Optional[int]:
        """El `ComboBox` con elementos y SIN `Edit` hijo.

        El del nombre de archivo y el de la barra de direcciones llevan un
        `Edit` dentro; el de tipo es una lista desplegable pura.
        """
        con_edit = {c["parent"] for c in arbol if c["class"] == "Edit"}
        for control in arbol:
            if control["class"] != "ComboBox" or control["hwnd"] in con_edit:
                continue
            if self._enviar(control["hwnd"], CB_GETCOUNT) > 0:
                return control["hwnd"]
        return None

    def _boton_guardar_en(self, arbol: List[Dict[str, Any]],
                          dialogo: int) -> Optional[int]:
        for control in arbol:
            if (control["class"] == "Button" and control["id"] == IDOK
                    and control["parent"] == dialogo):
                return control["hwnd"]
        return None

    def _es_cuadro_de_guardado(self, hwnd: int) -> bool:
        """Un cuadro de guardado tiene nombre de archivo Y tipo de archivo.

        Se pregunta por UI Automation y no por `GetDlgItem`: los controles del
        cuadro moderno cuelgan de contenedores y casi todos llevan id 0, asi
        que por Win32 no se distinguen de un cuadro de mensaje.
        """
        try:
            dialogo = self.uia.desde_hwnd(hwnd)
            return bool(self.uia.combo_de_nombre(dialogo)
                        and self.uia.combo_de_tipo(dialogo))
        except DesktopUINotAvailable:
            raise
        except Exception:                                 # noqa: BLE001
            return False

    def _enviar(self, hwnd: int, mensaje: int, wparam: int = 0,
                lparam: int = 0) -> int:
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = wintypes.LPARAM
        return int(user32.SendMessageW(wintypes.HWND(hwnd), mensaje,
                                       wparam, lparam))

    def _control(self, hwnd: int, control_id: int) -> Optional[int]:
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        user32.GetDlgItem.argtypes = [wintypes.HWND, wintypes.INT]
        user32.GetDlgItem.restype = wintypes.HWND
        handle = user32.GetDlgItem(wintypes.HWND(hwnd), control_id)
        return int(handle) if handle else None

    def _texto(self, hwnd: int) -> str:
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = wintypes.LPARAM
        largo = int(user32.SendMessageW(wintypes.HWND(hwnd),
                                        WM_GETTEXTLENGTH, 0, 0))
        buffer = ctypes.create_unicode_buffer(largo + 1)
        user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXT, largo + 1,
                            ctypes.cast(buffer, ctypes.c_void_p).value)
        return buffer.value

    def tipos_de_archivo(self, dialogo: Ventana) -> List[str]:
        import ctypes

        combo = self._combo_de_tipo_en(self._descendientes(dialogo.hwnd))
        if not combo:
            raise DesktopUIError(
                "El cuadro de guardado no expone el desplegable de tipo de "
                "archivo; no se acepta el tipo por defecto a ciegas.",
                details={"hwnd": dialogo.hwnd,
                         "reason": "file_type_combo_missing"})
        total = self._enviar(combo, CB_GETCOUNT)
        salida = []
        for indice in range(max(0, total)):
            largo = self._enviar(combo, CB_GETLBTEXTLEN, indice, 0)
            buffer = ctypes.create_unicode_buffer(largo + 1)
            self._enviar(combo, CB_GETLBTEXT, indice,
                         ctypes.cast(buffer, ctypes.c_void_p).value)
            salida.append(buffer.value)
        return salida

    def elegir_tipo(self, dialogo: Ventana, extension: str) -> str:
        """Se NIEGA: por mensajes Win32 el tipo no se puede comprometer.

        Elegir el tipo importa y no es una precaucion teorica -contra Power BI
        Desktop real el desplegable venia en `.pbip`, y aceptarlo habria
        entregado una carpeta de proyecto como si fuera el informe-. Lo que no
        sirve es esta via: se hace en `save_as_completo`.
        """
        raise DesktopUIError(
            _POR_QUE_NO_WIN32.format(paso="elegir el tipo de archivo"),
            details={"reason": "win32_does_not_commit",
                     "use_instead": "save_as_completo",
                     "requested": extension})

    def escribir_ruta(self, dialogo: Ventana, ruta: str) -> None:
        raise DesktopUIError(
            _POR_QUE_NO_WIN32.format(paso="escribir la ruta"),
            details={"reason": "win32_does_not_commit",
                     "use_instead": "save_as_completo"})

    def confirmar(self, dialogo: Ventana) -> None:
        raise DesktopUIError(
            _POR_QUE_NO_WIN32.format(paso="confirmar el guardado"),
            details={"reason": "win32_does_not_commit",
                     "use_instead": "save_as_completo"})


    def save_as_completo(self, *, pid: int, started: Optional[float],
                         destino: str, extension: str = ".pbix",
                         timeout: float = 180.0) -> Dict[str, Any]:
        """TODO el guardado, conducido desde un PROCESO APARTE.

        Es el camino real, y sustituye a la secuencia paso a paso de arriba
        por dos razones medidas contra Power BI Desktop:

        1. Los mensajes Win32 NO comprometen el tipo. `CB_SETCURSEL` cambia lo
           que se lee en el desplegable y no avisa a la aplicacion, asi que
           Desktop sigue guardando con el filtro anterior: se pedia `.pbix` y
           salia un proyecto `.pbip`. Hace falta UI Automation, o sea COM.
        2. COM no se puede importar en este proceso sin fijarle el apartamento
           al hilo, y una llamada COM que se bloquea no se cancela desde
           dentro. En otro proceso el plazo lo impone el sistema.

        Los metodos granulares se conservan porque siguen sirviendo para
        observar -enumerar controles, leer modales- y porque son lo que las
        pruebas recorren con un doble, sin abrir ninguna ventana.
        """
        from horizun_pbi_mcp.powerbi import desktop_helper

        return desktop_helper.ejecutar({
            "action": "save_as",
            "desktop_pid": int(pid),
            "desktop_started": started,
            "out_path": str(destino),
            "extension": extension,
            "dialog_timeout": min(60.0, timeout),
            "save_timeout": timeout,
            # Para que el helper distinga "el archivo ya aparecio en ESTA
            # ejecucion" de un destino que existia de antes.
            "started_at": time.time(),
        }, timeout=min(timeout, LIMITE_HELPER) + 60.0)

    def seleccionar_pagina(self, *, pid: int, started: Optional[float],
                           page_name: str,
                           timeout: float = LIMITE_NAVEGACION) -> Dict[str, Any]:
        """La pestaña se elige por UI Automation desde el proceso aparte.

        Es lo que evita cerrar la ventana, tocar `pages.json` y reabrir solo
        para fotografiar otra pagina. El helper devuelve `verified`: si la
        pestaña no expone su estado de seleccion, aqui NO se afirma nada.
        """
        from horizun_pbi_mcp.powerbi import desktop_helper

        return desktop_helper.ejecutar({
            "action": "select_page",
            "desktop_pid": int(pid),
            "desktop_started": started,
            "page_name": str(page_name),
        }, timeout=min(float(timeout), LIMITE_NAVEGACION) + 15.0)

    def ajustar_a_pagina(self, *, pid: int, started: Optional[float],
                         timeout: float = LIMITE_NAVEGACION) -> Dict[str, Any]:
        from horizun_pbi_mcp.powerbi import desktop_helper

        return desktop_helper.ejecutar({
            "action": "fit_to_page",
            "desktop_pid": int(pid),
            "desktop_started": started,
        }, timeout=min(float(timeout), LIMITE_NAVEGACION) + 15.0)

    def esperar_cierre(self, dialogo: Ventana, *, timeout: float) -> bool:
        import ctypes.wintypes
        wintypes = ctypes.wintypes

        user32 = _user32()
        user32.IsWindow.argtypes = [wintypes.HWND]
        limite = time.monotonic() + float(timeout)
        while time.monotonic() < limite:
            if not user32.IsWindow(wintypes.HWND(dialogo.hwnd)):
                return True
            time.sleep(0.2)
        return False

    def modales(self, pid: int, *,
                excluir: Sequence[int] = ()) -> List[Modal]:
        """Dialogos abiertos de ese proceso, clasificados y redactados."""
        fuera = {int(h) for h in excluir}
        salida: List[Modal] = []
        for ventana in self._ventanas(pid):
            if ventana.class_name.casefold() != CLASE_DIALOGO.casefold():
                continue
            if ventana.hwnd in fuera:
                continue
            if self._es_cuadro_de_guardado(ventana.hwnd):
                continue                      # es el cuadro de guardado
            estatico = self._control(ventana.hwnd, IDC_TEXTO_MENSAJE)
            texto = self._texto(estatico) if estatico else ""
            clase, accion = clasificar_modal(ventana.title, texto)
            salida.append(Modal(hwnd=ventana.hwnd,
                                title=redactar(ventana.title, maximo=120),
                                text=redactar(texto),
                                kind=clase, suggested_action=accion))
        return salida


def adaptador_por_defecto() -> AdaptadorUI:
    """El adaptador real. Falla claro donde no puede funcionar."""
    if os.name != "nt":
        raise DesktopUINotAvailable(
            "Exportar a .pbix conduce la interfaz de Power BI Desktop y eso "
            "solo existe en Windows.",
            details={"platform": os.name})
    return Win32UIAdapter()
