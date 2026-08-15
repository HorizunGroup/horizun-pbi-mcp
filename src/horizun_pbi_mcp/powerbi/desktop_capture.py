"""Captura fiable de una ventana concreta de Power BI Desktop.

La captura de validacion no puede depender de que Desktop tenga el foco: una
captura de pantalla normal puede fotografiar otra ventana, el escritorio o un
dialogo que aparecio entre medias. Este modulo identifica la ventana por el
PID y la hora de creacion que devolvio :mod:`desktop_launcher`, y usa
``PrintWindow`` para pedirle a Windows que renderice ESA ventana en memoria.

No activa, mueve, maximiza ni trae al frente la ventana. Tampoco cierra
procesos; esa responsabilidad sigue en ``desktop_launcher.close()``, que
conserva la regla de cerrar solo sesiones lanzadas por nosotros.
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from horizun_pbi_mcp.config import get_settings
from horizun_pbi_mcp.powerbi.errors import (PathSecurityError, PowerBIMCPError,
                                            ValidationError)
from horizun_pbi_mcp.utils.validation import validate_limit


class DesktopCaptureError(PowerBIMCPError):
    """La ventana exacta no pudo renderizarse de forma verificable."""

    code = "desktop_capture_failed"


# ------------------------------------------------ vista para la captura ------
def _definicion_de_report(pbip: Path, *, raiz: Path) -> Path:
    """Carpeta `definition/` del report que declara el propio .pbip.

    `artifacts[].report.path` lo escribe el archivo, no nosotros: es entrada no
    confiable y decide DONDE se escribe. Una ruta absoluta, un `..` o un
    symlink que salga de la carpeta del proyecto se rechazan aqui, antes de
    leer o escribir nada y antes de abrir Desktop o crear un journal.
    """
    import json

    from horizun_pbi_mcp.utils.validation import ensure_within_base

    datos = json.loads(pbip.read_text(encoding="utf-8-sig"))
    for artefacto in datos.get("artifacts", []) or []:
        ruta = ((artefacto or {}).get("report") or {}).get("path")
        if not ruta:
            continue
        candidato = Path(str(ruta))
        if candidato.is_absolute() or candidato.drive:
            raise PathSecurityError(
                "El .pbip declara el report con una ruta ABSOLUTA. El archivo "
                "no decide en que carpeta se escribe.",
                details={"path": str(pbip), "report_path": str(ruta)})
        # `ensure_within_base` resuelve los dos lados, asi que atrapa tanto
        # `..` como un symlink/junction que apunte fuera.
        definicion = ensure_within_base(raiz, raiz / candidato / "definition")
        if definicion.is_dir():
            return definicion
    raise ValidationError(
        "El .pbip no declara un report con carpeta definition/; no se puede "
        "preparar la vista de captura.", details={"path": str(pbip)})


def _leer_json(ruta: Path) -> dict:
    """JSON del archivo, o error. NUNCA se sobrescribe lo que no parsea.

    Se lee todo ANTES de escribir nada: si el segundo archivo del parche es
    ilegible, el primero no puede haber quedado ya modificado.
    """
    import json

    crudo = ruta.read_bytes()
    try:
        return json.loads(crudo.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"'{ruta.name}' no es JSON valido y no se sobrescribe: {exc}",
            details={"path": str(ruta), "rule": "no_overwrite_unreadable_json"}
        ) from exc


def _serializar(datos: dict) -> bytes:
    """Mismo formato que escribe Desktop: indent 2 y saltos CRLF."""
    import json

    return json.dumps(datos, indent=2).encode("utf-8").replace(b"\n", b"\r\n")


def _resolver_pagina_de_captura(paginas_dir: Path, page: str) -> str:
    """Id de la pagina pedida (por id o por nombre visible)."""
    import json

    candidatas = []
    for hija in sorted(paginas_dir.iterdir()):
        page_json = hija / "page.json"
        if not page_json.is_file():
            continue
        datos = json.loads(page_json.read_text(encoding="utf-8-sig"))
        if hija.name == page or datos.get("name") == page:
            return hija.name
        if str(datos.get("displayName") or "").casefold() == page.casefold():
            candidatas.append(hija.name)
    if len(candidatas) == 1:
        return candidatas[0]
    raise ValidationError(
        f"La pagina '{page}' no existe en el informe"
        + (" o su nombre visible esta repetido" if candidatas else "")
        + "; usa el id de la pagina.",
        details={"page": page, "matches": candidatas})


def planificar_vista_de_captura(path: str | Path, *,
                                page: Optional[str] = None,
                                fit_to_page: bool = True) -> dict[str, Any]:
    """Calcula el parche temporal de la captura SIN escribir nada.

    Sin esto la captura salia al zoom guardado y siempre de la pagina activa:
    solo se veia el tercio superior del lienzo, y el 15% inferior no se podia
    verificar nunca en remoto. `activePageName` decide que pagina abre Desktop
    y `displayOption: FitToPage` (esquema oficial de pagina 2.1.0) escala el
    lienzo completo al viewport.

    Devuelve `{"cambios": {ruta: bytes}, ...}`. Que sea un PLAN y no una
    escritura es la mitad de CORE-002: antes se escribia `pages.json` y solo
    despues se leia `page.json`, asi que un `page.json` ilegible dejaba el
    proyecto a medias. Ahora se lee y se decide todo primero, y quien aplica el
    plan lo hace como una unidad (`services.txn.parche_temporal`).

    TRAMPA, para quien copie esto a mano: `activePageName` vive en
    `pages/pages.json`. Escribirlo en `report.json` deja el informe abriendo EN
    BLANCO -sin error, con la validacion en verde y sin nada que lo explique-.
    Y aunque se escriba donde toca, hay que QUITARLO despues: es una vista para
    la captura, no una preferencia del informe.
    """
    pbip = Path(path).expanduser().resolve()
    raiz = pbip.parent
    definicion = _definicion_de_report(pbip, raiz=raiz)
    paginas_dir = definicion / "pages"
    pages_json = paginas_dir / "pages.json"
    if not pages_json.is_file():
        raise ValidationError(
            "El report no tiene pages.json; no se puede preparar la vista.",
            details={"path": str(pages_json)})

    metadatos = _leer_json(pages_json)
    pagina_id = (_resolver_pagina_de_captura(paginas_dir, page)
                 if page else str(metadatos.get("activePageName") or ""))

    cambios: dict[Path, bytes] = {}
    descripcion: list[str] = []

    if page and metadatos.get("activePageName") != pagina_id:
        propuesta = dict(metadatos)
        propuesta["activePageName"] = pagina_id
        cambios[pages_json] = _serializar(propuesta)
        descripcion.append(f"activePageName -> {pagina_id}")

    if fit_to_page and pagina_id:
        page_json = paginas_dir / pagina_id / "page.json"
        if page_json.is_file():
            datos = _leer_json(page_json)
            if datos.get("displayOption") != "FitToPage":
                propuesta = dict(datos)
                propuesta["displayOption"] = "FitToPage"
                cambios[page_json] = _serializar(propuesta)
                descripcion.append("displayOption -> FitToPage")

    return {"cambios": cambios, "page_id": pagina_id, "changes": descripcion}


@dataclass(frozen=True)
class DesktopWindow:
    hwnd: int
    pid: int
    title: str
    class_name: str
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


def _assert_desktop_identity(pid: int, expected_started: Optional[float]) -> None:
    """Falla cerrado si el PID ya no es el Desktop que abrimos/observamos."""
    import psutil

    if not pid or expected_started is None:
        raise DesktopCaptureError(
            "No se puede verificar la identidad del proceso de Power BI Desktop.",
            details={"pid": pid, "reason": "desktop_identity_unverifiable"},
        )
    try:
        process = psutil.Process(int(pid))
        name = process.name().casefold()
        started = float(process.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError) as exc:
        raise DesktopCaptureError(
            "El proceso de Power BI Desktop desaparecio o no se puede inspeccionar.",
            details={"pid": pid, "reason": "desktop_identity_unverifiable"},
        ) from exc
    if name != "pbidesktop.exe" or abs(started - float(expected_started)) > 1.0:
        raise DesktopCaptureError(
            "El PID de Power BI Desktop fue reutilizado; se rechazo la captura.",
            details={"pid": pid, "reason": "desktop_pid_reused",
                     "actual_process": name},
        )


def _enumerate_windows(pid: int) -> list[DesktopWindow]:
    """Enumera ventanas superiores visibles pertenecientes al PID exacto."""
    if os.name != "nt":
        raise DesktopCaptureError(
            "La captura de Power BI Desktop solo esta disponible en Windows.",
            details={"platform": os.name},
        )

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                      wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND,
                                     ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                      ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                     ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    windows: list[DesktopWindow] = []

    @callback_type
    def visit(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != int(pid) or not user32.IsWindowVisible(hwnd):
            return True

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 1 or height <= 1:
            return True

        title_len = max(0, int(user32.GetWindowTextLengthW(hwnd)))
        title_buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, len(class_buf))
        windows.append(DesktopWindow(
            hwnd=int(hwnd), pid=int(owner_pid.value), title=title_buf.value,
            class_name=class_buf.value, width=width, height=height))
        return True

    if not user32.EnumWindows(visit, 0):
        error = ctypes.get_last_error()
        raise DesktopCaptureError(
            "Windows no pudo enumerar las ventanas de Power BI Desktop.",
            details={"pid": pid, "winerror": error},
        )
    return windows


def _choose_window(windows: Iterable[DesktopWindow], report_path: str) -> DesktopWindow:
    """Elige la ventana del informe, no un splash o dialogo del mismo PID."""
    candidates = list(windows)
    if not candidates:
        raise DesktopCaptureError(
            "Power BI Desktop no tiene aun una ventana visible para capturar.",
            details={"path": report_path, "reason": "desktop_window_not_ready"},
        )
    stem = Path(report_path).stem.casefold()

    def score(window: DesktopWindow) -> tuple[int, int]:
        title = window.title.casefold()
        class_name = window.class_name.casefold()
        semantic = 0
        if stem and stem in title:
            semantic += 100
        if "power bi" in title:
            semantic += 30
        if "pbidesktop" in class_name:
            semantic += 20
        return semantic, window.area

    report_windows = [window for window in candidates
                      if stem and stem in window.title.casefold()]
    if report_windows:
        return max(report_windows, key=score)
    if len(candidates) == 1:
        return candidates[0]

    recognizable = [
        window for window in candidates
        if ("power bi" in window.title.casefold() or
            "pbidesktop" in window.class_name.casefold())
    ]
    if len(recognizable) == 1:
        return recognizable[0]
    # Capturar la ventana mas grande seria comodo, pero inseguro: un dialogo
    # de credenciales o incluso otro informe del mismo proceso puede ser mayor.
    raise DesktopCaptureError(
        "Hay varias ventanas de Power BI Desktop y ninguna identifica el informe.",
        details={
            "path": report_path,
            "reason": "desktop_window_ambiguous",
            "windows": [{"hwnd": window.hwnd, "title": window.title,
                         "class_name": window.class_name}
                        for window in candidates],
        },
    )


def _capture_window_bgra(hwnd: int) -> tuple[int, int, bytes]:
    """Renderiza ``hwnd`` en memoria mediante PrintWindow, sin usar el foco."""
    if os.name != "nt":
        raise DesktopCaptureError(
            "La captura de Power BI Desktop solo esta disponible en Windows.")

    import ctypes
    from ctypes import wintypes

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader),
                    ("bmiColors", wintypes.DWORD * 3)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    handle = ctypes.c_void_p
    user32.GetWindowRect.argtypes = [wintypes.HWND,
                                     ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = handle
    user32.ReleaseDC.argtypes = [wintypes.HWND, handle]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, handle, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.SelectObject.restype = handle
    gdi32.GetDIBits.argtypes = [handle, handle, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.POINTER(BitmapInfo),
                                wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [handle]
    gdi32.DeleteDC.restype = wintypes.BOOL
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise DesktopCaptureError(
            "Windows no pudo leer el tamano de la ventana de Desktop.",
            details={"hwnd": hwnd, "winerror": ctypes.get_last_error()})
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 1 or height <= 1 or width * height > 25_000_000:
        raise DesktopCaptureError(
            "La ventana tiene dimensiones no capturables.",
            details={"hwnd": hwnd, "width": width, "height": height})

    source_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    if not source_dc:
        raise DesktopCaptureError(
            "Windows no pudo obtener el contexto de la ventana de Desktop.",
            details={"hwnd": hwnd, "winerror": ctypes.get_last_error()})

    memory_dc = bitmap = previous = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(source_dc)
        bitmap = gdi32.CreateCompatibleBitmap(source_dc, width, height)
        if not memory_dc or not bitmap:
            raise DesktopCaptureError(
                "Windows no pudo reservar la imagen de captura.",
                details={"hwnd": hwnd, "width": width, "height": height,
                         "winerror": ctypes.get_last_error()})
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise DesktopCaptureError(
                "Windows no pudo preparar el bitmap de captura.",
                details={"hwnd": hwnd, "winerror": ctypes.get_last_error()})

        # PW_RENDERFULLCONTENT (2) pide a las ventanas modernas que rendericen
        # incluso si estan tapadas. El fallback 0 sigue siendo PrintWindow: no
        # lee pixeles de la pantalla ni depende del foco.
        rendered = user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 2)
        if not rendered:
            rendered = user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 0)
        if not rendered:
            raise DesktopCaptureError(
                "Power BI Desktop no renderizo su ventana para la captura.",
                details={"hwnd": hwnd, "reason": "print_window_failed",
                         "winerror": ctypes.get_last_error()})

        # GetDIBits exige que el bitmap no este seleccionado en ningun DC.
        # Restaurarlo antes de leer evita un resultado dependiente del driver.
        gdi32.SelectObject(memory_dc, previous)
        previous = None

        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # DIB top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = (ctypes.c_ubyte * (width * height * 4))()
        rows = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer,
                               ctypes.byref(info), 0)
        if rows != height:
            raise DesktopCaptureError(
                "Windows devolvio una captura incompleta de Desktop.",
                details={"hwnd": hwnd, "rows": rows, "expected_rows": height,
                         "winerror": ctypes.get_last_error()})
        pixels = bytes(buffer)
        # Un buffer completamente uniforme suele ser la superficie negra que
        # PrintWindow devuelve cuando la ventana aun no termino de renderizar.
        pixel_count = width * height
        pixel_step = max(1, pixel_count // 4096)
        samples = [pixels[index * 4:index * 4 + 3]
                   for index in range(0, pixel_count, pixel_step)]
        if (samples and
                all(sample == b"\x00\x00\x00" for sample in samples) and
                not any(pixels[0::4]) and not any(pixels[1::4]) and
                not any(pixels[2::4])):
            raise DesktopCaptureError(
                "Desktop devolvio un fotograma vacio; la vista aun no esta lista.",
                details={"hwnd": hwnd, "reason": "blank_frame"})
        return width, height, pixels
    finally:
        if previous and memory_dc:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(wintypes.HWND(hwnd), source_dc)


def _encode_png(width: int, height: int, bgra: bytes) -> bytes:
    """Codifica un DIB BGRA como PNG RGB usando solo la biblioteca estandar."""
    expected = width * height * 4
    if width <= 0 or height <= 0 or len(bgra) != expected:
        raise ValidationError(
            "Los pixeles de la captura no coinciden con sus dimensiones.",
            details={"width": width, "height": height,
                     "bytes": len(bgra), "expected_bytes": expected})

    stride_in = width * 4
    raw = bytearray()
    for row_index in range(height):
        row = bgra[row_index * stride_in:(row_index + 1) * stride_in]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw.append(0)  # filtro PNG "None"
        raw.extend(rgb)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", zlib.compress(bytes(raw), level=6)) +
            chunk(b"IEND", b""))


def _safe_stem(path: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(path).stem).strip("._")
    return value[:80] or "powerbi"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _fotograma_estable(hwnd: int, png: bytes, width: int, height: int,
                       segundos: float) -> tuple[int, int, bytes, bool]:
    """Repite la captura hasta que DOS fotogramas salgan identicos.

    Despues de un refresh, Desktop vuelve a lanzar las consultas de la pagina y
    tarda en repintar. No hay ningun evento que avisar desde fuera, asi que la
    sincronizacion se hace sobre lo unico observable: los pixeles. Esperar un
    plazo fijo seria adivinar; esto termina en cuanto la ventana deja de
    cambiar, y si nunca se estabiliza lo dice en vez de fingir.
    """
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        time.sleep(0.5)
        try:
            ancho, alto, pixeles = _capture_window_bgra(hwnd)
            actual = _encode_png(ancho, alto, pixeles)
        except (DesktopCaptureError, ValidationError):
            continue                      # aun repintando: se reintenta
        if actual == png and (ancho, alto) == (width, height):
            return width, height, png, True
        png, width, height = actual, ancho, alto
    return width, height, png, False


def capture_opened(opened: Any, *, timeout: int = 30,
                   output_dir: Optional[Path] = None,
                   settle_seconds: float = 0.0) -> dict[str, Any]:
    """Captura la ventana exacta asociada a un ``OpenedPbix`` verificado.

    Con `settle_seconds` se espera a que la ventana deje de cambiar antes de
    dar la captura por buena; es lo que hace falta justo despues de un refresh,
    cuando la pagina todavia se esta repintando.
    """
    timeout = validate_limit(timeout, "capture_timeout", 120)
    assert timeout is not None
    pid = getattr(opened, "desktop_pid", None)
    started = getattr(opened, "desktop_started", None)
    report_path = str(getattr(opened, "pbix_path", ""))
    if not pid or not report_path:
        raise DesktopCaptureError(
            "La sesion abierta no identifica proceso y archivo de Desktop.",
            details={"pid": pid, "path": report_path})

    deadline = time.monotonic() + timeout
    while True:
        _assert_desktop_identity(int(pid), started)
        try:
            window = _choose_window(_enumerate_windows(int(pid)), report_path)
            width, height, pixels = _capture_window_bgra(window.hwnd)
            png = _encode_png(width, height, pixels)
            break
        except DesktopCaptureError as exc:
            if time.monotonic() >= deadline:
                raise DesktopCaptureError(
                    f"Power BI Desktop no produjo una captura valida en {timeout} s.",
                    details={"pid": pid, "path": report_path,
                             "last_error": exc.code,
                             "last_details": exc.details},
                ) from exc
            time.sleep(0.5)

    estable: Optional[bool] = None
    if settle_seconds > 0:
        width, height, png, estable = _fotograma_estable(
            window.hwnd, png, width, height, float(settle_seconds))

    root = Path(output_dir) if output_dir is not None else (
        get_settings().outputs_dir / "desktop_captures")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = root / f"{_safe_stem(report_path)}_{stamp}_{uuid.uuid4().hex[:8]}.png"
    _write_atomic(target, png)
    salida = {
        "path": str(target.resolve()), "format": "png",
        "width": width, "height": height, "bytes": len(png),
        "desktop_pid": int(pid), "hwnd": window.hwnd,
        "window_title": window.title, "capture_method": "PrintWindow",
        "focus_required": False,
    }
    if estable is not None:
        salida["frame_settled"] = estable
    return salida
