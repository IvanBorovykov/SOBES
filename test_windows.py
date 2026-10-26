"""Windows regressions: real DPAPI and Win32 messages, synthetic audio, offline Tk UI."""

import sys
import unittest

if sys.platform != 'win32':
    raise unittest.SkipTest('Windows platform APIs')

import array
import ctypes
import io
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import assistant
import windows_platform as platform
from api_keys import ApiKey, KeyPool
from preferences import Preferences
from test_api_keys import MemoryStore
from windows_audio import PcmConverter, WindowsSystemAudio
from windows_credentials import WindowsKeyStore
from windows_ui import WindowsApp


class CredentialTests(unittest.TestCase):
    def test_dpapi_round_trip_contains_no_plaintext_and_keeps_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.dpapi'
            store = WindowsKeyStore(path)
            self.assertEqual(store.load()['entries'], [])
            data = {'entries': [{'id': 'one', 'name': 'Мій ключ', 'secret': 'dummy-secret-not-a-real-key'}],
                    'active': 'one', 'auto_switch': False}
            store.save(data)
            self.assertNotIn(b'dummy-secret-not-a-real-key', path.read_bytes())
            self.assertEqual(WindowsKeyStore(path).load(), data)

    def test_corrupt_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.dpapi'
            path.write_bytes(b'corrupt')
            with self.assertRaises(OSError):
                WindowsKeyStore(path).load()
            self.assertEqual(path.read_bytes(), b'corrupt')

    def test_failed_atomic_replace_keeps_previous_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WindowsKeyStore(Path(directory) / 'keys.dpapi')
            store.save({'entries': [], 'active': None})
            previous = store.path.read_bytes()
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(OSError):
                    store.save({'entries': [{'secret': 'dummy-new-secret'}]})
            self.assertEqual(store.path.read_bytes(), previous)
            self.assertEqual(list(store.path.parent.iterdir()), [store.path])


class AudioTests(unittest.TestCase):
    def test_stereo_right_channel_is_preserved_and_resampled(self):
        # A call may have speech on the right channel only.
        source = array.array('h', [0, 12000] * 4800).tobytes()
        converter = PcmConverter(48000, 2)
        frames = list(converter.feed(source))
        self.assertEqual(len(frames), 5)
        self.assertTrue(all(len(frame) == 640 for frame in frames))
        self.assertEqual(set(array.array('h', b''.join(frames))), {6000})

    def test_resampler_retains_state_across_native_chunks(self):
        source = array.array('h', (index % 2000 for index in range(44100))).tobytes()
        whole = b''.join(PcmConverter(44100, 1).feed(source))
        converter = PcmConverter(44100, 1)
        split = b''.join(frame for start in range(0, len(source), 882)
                         for frame in converter.feed(source[start:start + 882]))
        self.assertEqual(split, whole)
        self.assertEqual(len(whole), 32000)

    def test_multichannel_mix_includes_last_channel(self):
        source = array.array('h', [0, 0, 0, 0, 0, 12000] * 320).tobytes()
        frames = list(PcmConverter(16000, 6).feed(source))
        self.assertEqual(set(array.array('h', b''.join(frames))), {2000})

    def test_loopback_failure_is_reported_without_microphone_fallback(self):
        failed = threading.Event()
        errors = []
        ready = Mock()
        def report(message):
            errors.append(message)
            failed.set()
        with patch('windows_audio.pyaudio.PyAudio', side_effect=OSError('no WASAPI')):
            capture = WindowsSystemAudio(Mock(), report, ready)
            self.assertTrue(failed.wait(2))
            capture.close()
        ready.assert_not_called()
        self.assertIn('no WASAPI', errors[0])
        self.assertFalse(capture._worker.is_alive())

    def test_loopback_start_ready_frame_delivery_and_shutdown(self):
        delivered = threading.Event()
        ready, errors, received = Mock(), Mock(), []
        pa = Mock()
        pa.__enter__ = Mock(return_value=pa)
        pa.__exit__ = Mock(return_value=False)
        pa.get_default_wasapi_loopback.return_value = {'defaultSampleRate': 48000, 'maxInputChannels': 2, 'index': 7}
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.return_value = array.array('h', [0, 10000] * 960).tobytes()
        pa.open.return_value = stream
        def receive(data):
            received.append(data)
            delivered.set()
        with patch('windows_audio.pyaudio.PyAudio', return_value=pa):
            capture = WindowsSystemAudio(receive, errors, ready)
            self.assertTrue(delivered.wait(2))
            capture.close()
        ready.assert_called_once()
        errors.assert_not_called()
        self.assertEqual(set(array.array('h', received[0])), {5000})
        self.assertFalse(capture._worker.is_alive())
        self.assertEqual(pa.open.call_args.kwargs['input_device_index'], 7)
        stream.__exit__.assert_called_once()


class PlatformTests(unittest.TestCase):
    def test_disconnected_monitor_never_captures_another_display(self):
        with patch.object(platform, 'get_displays', return_value=[]), patch.object(platform.mss, 'mss') as capture:
            with self.assertRaisesRegex(ValueError, 'отключён'):
                platform.capture_screenshot('missing')
            capture.assert_not_called()

    def test_capture_uses_selected_monitor_with_negative_coordinates(self):
        bounds = {'left': -1920, 'top': -1080, 'width': 1920, 'height': 1080}
        shot = Mock(size=(32, 24), bgra=bytes(32 * 24 * 4))
        capture = Mock()
        capture.__enter__ = Mock(return_value=capture)
        capture.__exit__ = Mock(return_value=False)
        capture.grab.return_value = shot
        with patch.object(platform, 'get_displays', return_value=[{'id': 'second', 'region': bounds}]), \
                patch.object(platform.mss, 'mss', return_value=capture):
            data = platform.capture_screenshot('second', fast=True)
        capture.grab.assert_called_once_with(bounds)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (32, 24))

    def test_real_hotkey_registration_message_dispatch_and_cleanup(self):
        callback = Mock()
        hotkeys = platform.Hotkeys({'A': callback})
        self.addCleanup(hotkeys.close)
        if hotkeys.errors:
            self.skipTest('Alt+A is already registered by another application')
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentThreadId.restype = ctypes.c_ulong
        platform.user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint,
                                                       ctypes.c_size_t, ctypes.c_ssize_t]
        self.assertTrue(platform.user32.PostThreadMessageW(kernel.GetCurrentThreadId(), 0x0312, 1, 0))
        hotkeys.poll()
        callback.assert_called_once()
        hotkeys.close()
        self.assertEqual(hotkeys.registered, [])

    def test_failed_hotkeys_are_visible(self):
        with patch.object(platform.user32, 'RegisterHotKey', return_value=0):
            hotkeys = platform.Hotkeys({'Q': Mock()})
        self.assertIn('Alt+Q', hotkeys.errors[0])
        hotkeys.close()

    def test_worker_dispatch_waits_for_main_thread(self):
        callback = Mock()
        thread = threading.Thread(target=lambda: platform.AppHelper.callAfter(callback, 'text'))
        thread.start()
        thread.join()
        callback.assert_not_called()
        platform.AppHelper.drain()
        callback.assert_called_once_with('text')


class WindowsUiTests(unittest.TestCase):
    def button(self, window, text):
        for child in window.winfo_children():
            if child.winfo_class() == 'TButton' and child.cget('text') == text:
                return child
            found = self.button(child, text)
            if found is not None:
                return found
        return None

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        preferences = Preferences(Path(self.temporary.name) / 'settings.json')
        entry = ApiKey('test', 'Dummy', 'test-placeholder-api-key')
        self.keys = KeyPool(MemoryStore([entry], entry.id))
        self.audio = Mock(source='microphone', error='', device_name='Offline test microphone')
        self.app = WindowsApp(assistant, preferences=preferences, keys=self.keys,
                              audio=self.audio, start_realtime=False)
        self.addCleanup(self.app.close)
        self.app.root.update()

    def test_offline_window_dialogs_and_shutdown(self):
        self.app.open_settings()
        self.app.open_files()
        self.app.open_keys()
        self.app.root.update()
        self.assertTrue(all(w.winfo_exists() for w in self.app._dialogs.values()))
        self.assertEqual(self.app.assistant.context_files, ())
        self.app.close()
        self.audio.close.assert_called_once()
        self.assertEqual(self.app.hotkeys.registered, [])
        self.assertTrue(self.app.closed)

    def test_background_render_and_clear_ignore_stale_updates(self):
        generation = self.app.assistant._generation
        thread = threading.Thread(target=lambda: self.app.assistant._show(generation, 'answer from worker'))
        thread.start()
        thread.join()
        platform.AppHelper.drain()
        self.assertIn('answer from worker', self.app.overlay.text.get('1.0', 'end'))
        self.app.assistant.clear()
        self.app.assistant._show(generation, 'stale answer')
        platform.AppHelper.drain()
        self.assertNotIn('stale answer', self.app.overlay.text.get('1.0', 'end'))

    def test_unplugged_capture_stays_blocked_but_output_windows_recover(self):
        self.app.preferences.capture_display = 'missing monitor'
        self.app.preferences.overlay_display = 'missing monitor'
        self.app.preferences.overlay_origin = [100000, 100000]
        self.app.restore_layout()
        self.assertEqual(self.app.assistant.capture_display_id, -1)
        self.assertIn(self.app.preferences.overlay_display, [d['key'] for d in platform.get_displays()])
        self.app.remember_windows()
        saved = Preferences(self.app.preferences.path)
        self.assertEqual(saved.capture_display, 'missing monitor')
        self.assertLess(saved.overlay_origin[0], 100000)

    def test_settings_apply_reconnects_audio_and_saves(self):
        self.app.open_settings()
        window = self.app._dialogs['settings']
        self.button(window, 'Применить').invoke()
        self.audio.set_source.assert_called_once_with('microphone')
        self.audio.set_seconds.assert_called_with(30)
        self.assertTrue(self.app.preferences.path.exists())

    def test_files_add_deduplicates_and_remove_leaves_original(self):
        path = Path(self.temporary.name) / 'context.txt'
        path.write_text('answer is 42', encoding='utf-8')
        self.app.open_files()
        window = self.app._dialogs['files']
        with patch('windows_ui.filedialog.askopenfilenames', return_value=(str(path), str(path))):
            self.button(window, 'Добавить…').invoke()
        self.assertEqual(self.app.assistant.context_files, (str(path.resolve()),))
        self.button(window, 'Убрать все').invoke()
        self.assertEqual(self.app.assistant.context_files, ())
        self.assertTrue(path.is_file())

    def test_keys_save_existing_and_add_profile_without_showing_saved_secret(self):
        self.app.open_keys()
        window = self.app._dialogs['keys']
        self.button(window, 'Сохранить и использовать').invoke()
        self.assertEqual(self.keys.entries()[0].secret, 'test-placeholder-api-key')
        self.button(window, 'Добавить ключ').invoke()
        frame = window.winfo_children()[0]
        entries = [widget for widget in frame.winfo_children() if widget.winfo_class() == 'TEntry']
        self.assertEqual(entries[2].get(), '')
        entries[0].delete(0, 'end')
        entries[0].insert(0, 'Second')
        entries[2].insert(0, 'dummy-second-api-key')
        self.button(window, 'Сохранить и использовать').invoke()
        self.assertEqual(len(self.keys.entries()), 2)
        self.assertEqual(self.keys.entries()[-1].name, 'Second')
        self.assertEqual(self.keys.active_id, self.keys.entries()[-1].id)
        self.assertEqual(entries[2].get(), '')

    def test_first_key_has_clear_profile_label_and_accepts_clipboard_paste(self):
        self.keys.replace([], None, True)
        self.app.open_keys()
        window = self.app._dialogs['keys']
        self.app.root.update()
        frame = window.winfo_children()[0]
        listing = next(widget for widget in frame.winfo_children() if widget.winfo_class() == 'TCombobox')
        entries = [widget for widget in frame.winfo_children() if widget.winfo_class() == 'TEntry']
        secret = entries[2]
        self.assertIn('Новый ключ', listing.get())
        self.assertEqual(str(secret.cget('state')), 'normal')
        self.assertEqual(window.focus_get(), secret)
        # WM_CHAR carries actual text even when Tk cannot map a synthetic keysym
        # for the current Windows keyboard layout.
        platform.user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                ctypes.c_size_t, ctypes.c_ssize_t]
        self.assertTrue(platform.user32.PostMessageW(secret.winfo_id(), 0x0102, ord('a'), 1))
        self.app.root.update()
        self.assertEqual(secret.get(), 'a')
        with patch.object(window, 'clipboard_get', return_value='  dummy-first-api-key  '):
            self.button(window, 'Вставить ключ из буфера').invoke()
        self.assertEqual(secret.get(), 'dummy-first-api-key')
        self.button(window, 'Сохранить и использовать').invoke()
        self.assertEqual(len(self.keys.entries()), 1)
        self.assertEqual(self.keys.entries()[0].secret, 'dummy-first-api-key')
        self.assertEqual(secret.get(), '')

    def test_secret_paste_uses_physical_control_v_binding(self):
        self.app.open_keys()
        window = self.app._dialogs['keys']
        self.app.root.update()
        frame = window.winfo_children()[0]
        secret = [widget for widget in frame.winfo_children() if widget.winfo_class() == 'TEntry'][2]
        with patch.object(window, 'clipboard_get', return_value='dummy-physical-paste-key') as clipboard:
            secret.event_generate('<Control-KeyPress>', keycode=86)
        clipboard.assert_called_once()
        self.assertEqual(secret.get(), 'dummy-physical-paste-key')


if __name__ == '__main__':
    unittest.main()
