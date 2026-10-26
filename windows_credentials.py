"""Atomic credential storage encrypted for the current Windows user with DPAPI."""

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from preferences import DEFAULT_PATH


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; never allow a background credential prompt.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class WindowsKeyStore:
    def __init__(self, path=None, error_type=OSError):
        self.path = Path(path) if path is not None else DEFAULT_PATH.with_name('api-keys.dpapi')
        self.error_type = error_type

    def load(self):
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return {'entries': [], 'active': None, 'auto_switch': True}
        except OSError as error:
            raise self.error_type('Не удалось открыть хранилище API-ключей Windows.') from error
        try:
            data = json.loads(protect(encrypted, decrypt=True).decode('utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
                raise ValueError('Invalid credential data')
            return data
        except (OSError, ValueError, UnicodeError) as error:
            raise self.error_type('Не удалось расшифровать API-ключи. Используйте тот же профиль Windows; '
                                  'повреждённое хранилище не изменено.') from error

    def save(self, data):
        temporary = None
        try:
            encrypted = protect(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        except OSError as error:
            raise self.error_type('Не удалось сохранить API-ключи в Windows.') from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
