"""Windows displays, capture, UI dispatch and native global hotkeys."""

import ctypes
import io
import queue
from ctypes import wintypes

import mss
from PIL import Image

user32 = ctypes.WinDLL('user32', use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except AttributeError:
    user32.SetProcessDPIAware()


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR * 32)]


MonitorCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MonitorCallback, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                              wintypes.UINT, wintypes.UINT, wintypes.UINT]


def get_displays():
    result = []

    def collect(handle, dc, rect, parameter):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            def region(rectangle):
                return {'left': rectangle.left, 'top': rectangle.top,
                        'width': rectangle.right - rectangle.left, 'height': rectangle.bottom - rectangle.top}
            bounds = region(info.monitor)
            result.append({'id': info.device, 'key': info.device, 'name': info.device,
                           'region': bounds, 'work': region(info.work), 'primary': bool(info.flags & 1),
                           'title': f'{info.device} — {bounds["width"]} × {bounds["height"]}'})
        return True

    if not user32.EnumDisplayMonitors(None, None, MonitorCallback(collect), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(result, key=lambda item: (not item['primary'], item['key']))


def capture_screenshot(display_id=None, fast=False):
    displays = get_displays()
    display = next((item for item in displays if item['id'] == display_id), None) if display_id is not None else (
        displays[0] if displays else None)
    if display is None:
        raise ValueError('Экран для анализа отключён. Выберите экран в настройках.')
    with mss.mss() as capture:
        shot = capture.grab(display['region'])
        picture = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
    if fast:
        picture.thumbnail((1280, 1280), Image.Resampling.BILINEAR)
    elif picture.width > 1280:
        picture = picture.resize((1280, max(1, round(picture.height * 1280 / picture.width))), Image.Resampling.LANCZOS)
    while True:
        output = io.BytesIO()
        picture.save(output, 'JPEG', quality=60 if fast else 70, optimize=not fast)
        data = output.getvalue()
        if not fast or len(data) <= 100_000 or picture.width <= 320:
            return data
        picture = picture.resize((max(1, picture.width * 4 // 5), max(1, picture.height * 4 // 5)),
                                 Image.Resampling.BILINEAR)


def window_handle(window):
    window.update_idletasks()
    return user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT, including Tk's outer frame


def exclude_from_capture(window):
    return bool(user32.SetWindowDisplayAffinity(window_handle(window), 0x11))


def place_window(window, x, y):
    user32.SetWindowPos(window_handle(window), None, int(x), int(y), 0, 0, 0x0015)  # NOSIZE/NOZORDER/NOACTIVATE


class AppHelper:
    """Workers enqueue Python calls; only Tk's owning thread touches widgets."""
    pending = queue.Queue()

    @classmethod
    def callAfter(cls, callback, *args):
        cls.pending.put((callback, args))

    @classmethod
    def drain(cls):
        for _ in range(100):
            try:
                callback, args = cls.pending.get_nowait()
            except queue.Empty:
                break
            callback(*args)


class Hotkeys:
    def __init__(self, actions):
        self.actions, self.registered, self.errors = actions, [], []
        for index, (letter, callback) in enumerate(actions.items(), 1):
            if user32.RegisterHotKey(None, index, 0x0001 | 0x4000, ord(letter.upper())):  # ALT | NOREPEAT
                self.registered.append(index)
            else:
                self.errors.append(f'Alt+{letter.upper()} занят другим приложением (код {ctypes.get_last_error()})')

    def poll(self):
        message = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):  # WM_HOTKEY, PM_REMOVE
            index = int(message.wParam)
            if index in self.registered:
                list(self.actions.values())[index - 1]()

    def close(self):
        for index in self.registered:
            user32.UnregisterHotKey(None, index)
        self.registered.clear()
