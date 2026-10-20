import io
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

import assistant
from system_audio import SystemAudio


class SystemAudioTests(unittest.TestCase):
    def test_pipe_fragments_are_reassembled_into_exact_20ms_frames(self):
        chunks = []
        fragments = iter([b'a' * 100, b'b' * 540, b'c' * 640, b'd' * 30, b''])
        pipe = Mock()
        pipe.read.side_effect = lambda size: next(fragments)
        reader = SimpleNamespace(_stopped=False, _process=SimpleNamespace(stdout=pipe), _on_audio=chunks.append)
        SystemAudio._read(reader)
        self.assertEqual(chunks, [b'a' * 100 + b'b' * 540, b'c' * 640])
        pipe.close.assert_called_once()

    def make_ring(self):
        ring = assistant.AudioRing.__new__(assistant.AudioRing)
        ring._chunks = deque([b'old microphone bytes'])
        ring._lock = threading.Lock()
        ring._seconds = 8
        ring._listeners = []
        ring._stream = ring._pa = ring._system = None
        ring.source = 'microphone'
        ring.error = ''
        ring.on_status = Mock()
        return ring

    def test_system_source_stops_mic_and_does_not_start_another_microphone(self):
        ring = self.make_ring()
        microphone = ring._stream = Mock()
        pa = ring._pa = Mock()
        received = []
        ring.subscribe(received.append)
        with patch.object(assistant, 'SystemAudio') as native, patch.object(ring, '_start_microphone') as start:
            ring.set_source('system')
            native.call_args.args[2]()
            microphone.stop_stream.assert_called_once()
            microphone.close.assert_called_once()
            pa.terminate.assert_called_once()
            start.assert_not_called()
            self.assertEqual(ring.pcm_bytes(), b'')
            native.call_args.args[0](b'\x01\x00' * 320)
            self.assertEqual(received, [b'\x01\x00' * 320])
            self.assertEqual(ring.pcm_bytes(), received[0])
            ring.close()

    def test_capture_failure_blocks_audio_instead_of_using_old_buffer(self):
        ring = self.make_ring()
        ring._source_error('Permission denied')
        self.assertFalse(ring._chunks)
        ring.on_status.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'Permission denied'):
            ring.pcm_bytes()

    def test_failed_source_can_be_retried(self):
        ring = self.make_ring()
        ring.source = 'system'
        ring.error = 'Permission denied'
        with patch.object(assistant, 'SystemAudio') as native:
            ring.set_source('system')
            native.assert_called_once()
            native.call_args.args[2]()
            self.assertEqual(ring.error, '')
            ring.close()

    def test_ready_is_not_an_error_but_unexpected_exit_is(self):
        errors = []
        reader = SimpleNamespace(_stopped=False, _process=SimpleNamespace(
            stderr=io.BytesIO(b'READY\nPermission denied\n'), wait=lambda: 1), _on_error=errors.append, _on_ready=Mock())
        SystemAudio._read_errors(reader)
        self.assertIn('Permission denied', errors)
        reader._on_ready.assert_called_once()
        self.assertNotIn('READY', errors)

    def test_temporarily_missing_display_waits_and_recovers_on_ready(self):
        ring = self.make_ring()
        reader = SimpleNamespace(_stopped=False, _process=SimpleNamespace(
            stderr=io.BytesIO(b'WAITING_DISPLAY\nREADY\n'), wait=lambda: 0),
            _on_error=ring._source_error, _on_ready=ring._source_ready)
        def mark_ready():
            self.assertIn('Ожидаю доступный дисплей', ring.error)
            ring._source_ready()
            self.assertEqual(ring.error, '')
            reader._stopped = True
        reader._on_ready = mark_ready
        SystemAudio._read_errors(reader)
        self.assertFalse(ring._chunks)
        self.assertEqual(ring.error, '')

    def test_startup_work_does_not_block_constructor(self):
        entered, release = threading.Event(), threading.Event()
        def startup(instance):
            entered.set()
            release.wait(timeout=2)
        with patch.object(SystemAudio, '_start', startup):
            capture = SystemAudio(Mock(), Mock())
            try:
                self.assertTrue(entered.wait(timeout=1))
                self.assertTrue(capture._worker.is_alive())
            finally:
                release.set()
                capture.close()
