import io
import time
import threading
import unittest
import wave
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

import assistant
from api_keys import ApiKey, KeyPool
from test_api_keys import MemoryStore


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.overlay = Mock()
        self.audio = Mock()
        self.audio.wav_bytes.return_value = b"wav"
        self.audio.pcm_bytes.return_value = b"pcm"
        live = patch.object(assistant, "LiveConnection")
        self.live_factory = live.start()
        self.addCleanup(live.stop)
        self.client_patch = patch("api_keys.genai.Client", return_value=self.client)
        self.client_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        capture = patch.object(assistant, "capture_screenshot", return_value=b"jpeg")
        self.capture = capture.start()
        self.addCleanup(capture.stop)
        dispatch = patch.object(assistant.AppHelper, "callAfter", side_effect=lambda fn, *args: fn(*args))
        dispatch.start()
        self.addCleanup(dispatch.stop)
        models = patch.multiple(assistant, PRIMARY_MODEL="gemini-3.5-flash-lite", FALLBACK_MODEL="gemini-3.8-flash")
        models.start()
        self.addCleanup(models.stop)
        self.entry = ApiKey("test", "Тестовый", "test-placeholder-api-key")
        self.keys = KeyPool(MemoryStore([self.entry], self.entry.id))
        self.app = assistant.Assistant(self.overlay, self.audio, self.keys)
        self.addCleanup(self.app.close)
        self.generate = self.client.models.generate_content_stream

    def run_request(self, screen_only=False):
        self.app._run(self.app._generation, time.perf_counter(), screen_only)

    def shown(self):
        return [call.args[0] for call in self.overlay.show_text.call_args_list]

    def test_live_permission_failure_still_allows_rest_answer(self):
        self.keys.failed_client(self.client, assistant.LIVE_MODEL,
                                assistant.errors.APIError(403, {"message": "Permission denied"}))
        self.generate.return_value = iter([SimpleNamespace(text="REST works")])
        self.run_request()
        self.assertIn("REST works", self.shown()[-1])

    def test_clear_does_not_reconnect_a_key_in_live_cooldown(self):
        self.keys.failed_client(self.client, assistant.LIVE_MODEL,
                                assistant.errors.APIError(429, {"message": "Quota"}))
        self.app.clear()
        self.live_factory.return_value.reset.assert_called_once_with(None)

    def test_first_text_is_shown_before_stream_finishes(self):
        def stream():
            yield SimpleNamespace(text="Перша відповідь")
            self.assertTrue(any("Перша відповідь" in value for value in self.shown()))
            yield SimpleNamespace(text=" та деталі")

        self.generate.return_value = stream()
        self.run_request()
        self.assertIn("Перша відповідь та деталі", self.shown()[-1])
        self.assertIn("перший текст", self.shown()[-1])
        self.assertIn("Завершено", self.shown()[-1])

    def test_screen_only_never_reads_or_uploads_audio(self):
        self.generate.return_value = (SimpleNamespace(text="OK") for _ in range(1))
        self.run_request(screen_only=True)
        self.audio.wav_bytes.assert_not_called()
        contents = self.generate.call_args.kwargs["contents"]
        self.assertEqual(contents[0].inline_data.mime_type, "image/jpeg")
        self.assertEqual(len(contents), 2)
        self.assertIn("Аудіо не додано", contents[-1])

    def test_audio_preparation_overlaps_screen_capture(self):
        capture_started, audio_ready = threading.Event(), threading.Event()

        def capture(display_id=None):
            capture_started.set()
            self.assertTrue(audio_ready.wait(timeout=1))
            return b"jpeg"

        def audio():
            self.assertTrue(capture_started.wait(timeout=1))
            audio_ready.set()
            return b"wav"

        self.capture.side_effect = capture
        self.audio.wav_bytes.side_effect = audio
        self.generate.return_value = (SimpleNamespace(text="OK") for _ in range(1))
        self.run_request()
        self.assertEqual(self.generate.call_args.kwargs["contents"][0].inline_data.mime_type, "audio/wav")

    def test_transient_errors_switch_models_without_sdk_retries(self):
        for code in (404, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.generate.reset_mock()
                self.generate.side_effect = [
                    assistant.errors.APIError(code, {"message": "Unavailable"}),
                    (SimpleNamespace(text="Запасна відповідь") for _ in range(1)),
                ]
                self.run_request()
                calls = self.generate.call_args_list
                self.assertEqual([c.kwargs["model"] for c in calls], [assistant.PRIMARY_MODEL, assistant.FALLBACK_MODEL])
                self.assertEqual(calls[0].kwargs["config"].thinking_config.thinking_level, "MINIMAL")
                self.assertEqual(calls[1].kwargs["config"].thinking_config.thinking_level, "LOW")
                self.assertIn("Запасна відповідь", self.shown()[-1])
        options = self.client_factory.call_args.kwargs["http_options"]
        self.assertEqual(options.retry_options.attempts, 1)

    def test_rate_limit_switches_key_before_falling_back_to_another_model(self):
        second = ApiKey("second", "Запасной", "test-secondary-api-key")
        self.keys.replace([self.entry, second], self.entry.id, True)
        next_client = Mock()
        self.client_factory.return_value = next_client
        self.generate.side_effect = assistant.errors.APIError(429, {"message": "Rate limit"})
        next_client.models.generate_content_stream.return_value = (SimpleNamespace(text="Ответ второго ключа") for _ in range(1))
        self.run_request()
        self.assertIn("Ответ второго ключа", self.shown()[-1])
        self.assertEqual(next_client.models.generate_content_stream.call_args.kwargs["model"], assistant.PRIMARY_MODEL)
        self.assertEqual(self.keys.active_id, second.id)
        self.capture.assert_called_once()

    def test_partial_answer_is_preserved_when_stream_fails(self):
        def stream():
            yield SimpleNamespace(text="Часткова відповідь")
            raise assistant.errors.APIError(503, {"message": "Unavailable"})

        self.generate.return_value = stream()
        self.run_request()
        self.generate.assert_called_once()
        self.assertIn("Часткова відповідь", self.shown()[-1])
        self.assertIn("503", self.shown()[-1])

    def test_live_hint_shows_transcript_and_link_to_code_hotkey(self):
        self.app._live.stream.return_value = (part for part in ["Коротка", " підказка"])
        self.app._run_live(0, time.perf_counter())
        self.assertIn("Коротка підказка", self.shown()[-1])
        self.assertIn("деталі / код", self.shown()[-1])
        self.generate.assert_not_called()

    def test_live_failure_before_text_uses_regular_text_stream(self):
        self.app._live.stream.side_effect = ConnectionError("offline")
        self.generate.return_value = (SimpleNamespace(text="Text fallback") for _ in range(1))
        self.app._run_live(0, time.perf_counter())
        self.assertIn("Text fallback", self.shown()[-1])
        self.capture.assert_called_once()
        self.audio.wav_bytes.assert_called_once_with(b"pcm")

    def test_live_failure_after_text_preserves_hint(self):
        def stream():
            yield "Підказка"
            raise ConnectionError("offline")

        self.app._live.stream.return_value = stream()
        self.app._run_live(0, time.perf_counter())
        self.assertIn("Підказка", self.shown()[-1])
        self.generate.assert_not_called()

    def test_clear_closes_stream_and_does_not_redisplay_overlay(self):
        closed = []

        def stream():
            try:
                yield SimpleNamespace(text="Початок")
                self.app.clear()
                yield SimpleNamespace(text="Застаріла відповідь")
            finally:
                closed.append(True)

        self.generate.return_value = stream()
        self.run_request()
        self.assertEqual(closed, [True])
        self.overlay.hide.assert_called_once()
        self.assertFalse(any("Застаріла" in value or "Завершено" in value for value in self.shown()))

    def test_cancel_during_capture_prevents_network_request(self):
        self.capture.side_effect = lambda display_id=None: (self.app.clear(), b"jpeg")[1]
        self.run_request()
        self.generate.assert_not_called()

    def test_queued_stale_text_cannot_overwrite_new_request(self):
        self.app._generation = 2
        self.app._show_if_current(1, "stale")
        self.overlay.show_text.assert_not_called()

    def test_hotkey_freezes_capture_monitor_before_the_worker_starts(self):
        self.app.capture_display_id = 3
        self.generate.return_value = (SimpleNamespace(text="OK") for _ in range(1))
        with patch.object(assistant.threading, "Thread") as thread:
            self.app.analyze(screen_only=True)
        arguments = thread.call_args.kwargs
        self.app.capture_display_id = 1
        arguments["target"](*arguments["args"], **arguments["kwargs"])
        self.capture.assert_called_once_with(3)

    def test_same_primary_and_fallback_are_not_requested_twice(self):
        self.generate.side_effect = assistant.errors.APIError(429, {"message": "Rate limit"})
        with patch.object(assistant, "FALLBACK_MODEL", assistant.PRIMARY_MODEL):
            self.run_request()
        self.generate.assert_called_once()


class AudioBufferTests(unittest.TestCase):
    def test_pcm_and_wav_keep_the_same_exact_recent_window(self):
        ring = assistant.AudioRing.__new__(assistant.AudioRing)
        ring._lock = threading.Lock()
        ring._seconds = assistant.BUFFER_SECONDS
        length = assistant.BUFFER_SECONDS * assistant.SAMPLE_RATE * assistant.SAMPLE_WIDTH
        ring._chunks = deque([b"\x01\x02" * (length // 2), b"\x03\x04" * 512])
        pcm = ring.pcm_bytes()
        self.assertEqual(len(pcm), length)
        self.assertTrue(pcm.endswith(b"\x03\x04" * 512))
        with wave.open(io.BytesIO(ring.wav_bytes(pcm)), "rb") as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)

    def test_longer_window_recovers_audio_already_kept_in_memory(self):
        ring = assistant.AudioRing.__new__(assistant.AudioRing)
        ring._lock = threading.Lock()
        bytes_per_second = assistant.SAMPLE_RATE * assistant.SAMPLE_WIDTH
        ring._chunks = deque(bytes([index]) * bytes_per_second for index in range(40))
        ring.set_seconds(8)
        self.assertEqual(len(ring.pcm_bytes()), 8 * bytes_per_second)
        ring.set_seconds(30)
        self.assertEqual(len(ring.pcm_bytes()), 30 * bytes_per_second)
        self.assertEqual(ring.pcm_bytes()[0], 10)


if __name__ == "__main__":
    unittest.main()
