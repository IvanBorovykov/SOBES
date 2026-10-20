import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from google.genai import types

from api_keys import ApiKey, KeyPool
from realtime_hints import Hint, RealtimeHints
from test_api_keys import MemoryStore


def message(text=None, complete=False, input_text=None, interrupted=False, activity=None):
    return types.LiveServerMessage(
        voice_activity=types.VoiceActivity(voice_activity_type=activity) if activity else None,
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text=text) if text else None,
            input_transcription=types.Transcription(text=input_text) if input_text else None,
            turn_complete=complete, interrupted=interrupted,
        ),
    )


class FakeSession:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.inputs = []
        self.audio_sent = threading.Event()
        self.closed = threading.Event()

    async def send_realtime_input(self, **kwargs):
        self.inputs.append(kwargs)
        if "audio" in kwargs:
            self.audio_sent.set()

    async def receive(self):
        while True:
            item = await self.messages.get()
            if isinstance(item, Exception):
                raise item
            yield item
            if item.server_content and item.server_content.turn_complete:
                return


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.ready = threading.Event()
        self.client = Mock()
        self.contexts = []

        def connect(**kwargs):
            session = FakeSession()
            self.sessions.append(session)
            context = Mock()
            context.__aenter__ = AsyncMock(return_value=session)
            context.__aexit__ = AsyncMock(side_effect=lambda *args: session.closed.set())
            self.contexts.append(context)
            return context

        self.client.aio.live.connect.side_effect = connect
        patcher = patch("api_keys.genai.Client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        entry = ApiKey("test", "Test", "dummy-not-a-real-key")
        self.keys = KeyPool(MemoryStore([entry], entry.id))
        self.capture = Mock(return_value=b"jpeg")
        self.engine = None

        def status():
            if self.engine and all(ch.hint.ready for ch in self.engine._channels.values()):
                self.ready.set()

        self.engine = RealtimeHints(self.keys, "gemini-3.8-live", "Short answer",
                                    self.capture, 3, status)
        self.addCleanup(self.engine.close)
        self.assertTrue(self.ready.wait(3), "Both isolated streams should connect")

    def emit(self, session, item):
        self.engine._loop.call_soon_threadsafe(session.messages.put_nowait, item)

    def wait_hint(self, audio_only, predicate):
        deadline = time.monotonic() + 2
        with self.engine._condition:
            while not predicate(self.engine._channels[audio_only].hint):
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "Expected transcript state")
                self.engine._condition.wait(remaining)
            return self.engine._channels[audio_only].hint

    def audio_session(self):
        return next(session for session in self.sessions if not any("video" in item for item in session.inputs))

    def test_streams_20ms_audio_before_hotkey_and_keeps_audio_only_context_clean(self):
        pcm = b"\x01\x02" * 320
        self.engine.offer_audio(pcm)
        for session in self.sessions:
            self.assertTrue(session.audio_sent.wait(1))
            self.assertEqual(next(item["audio"].data for item in session.inputs if "audio" in item), pcm)
        audio = self.audio_session()
        self.assertFalse(any("video" in item for item in audio.inputs))
        screen = next(session for session in self.sessions if session is not audio)
        self.assertIn("video", screen.inputs[0])
        self.assertFalse(any("activity_start" in item or "activity_end" in item for item in screen.inputs))
        config = self.client.aio.live.connect.call_args.kwargs["config"]
        self.assertEqual(config.realtime_input_config.automatic_activity_detection.silence_duration_ms, 350)
        self.assertFalse(config.realtime_input_config.automatic_activity_detection.disabled)
        self.assertEqual(config.media_resolution, types.MediaResolution.MEDIA_RESOLUTION_LOW)
        self.assertIsNone(config.thinking_config)

    def test_hotkey_flush_follows_already_queued_audio(self):
        channel = self.engine._channels[True]
        session = self.audio_session()
        finished = threading.Event()

        def submit():
            self.engine._begin_turn(channel)
            self.engine.offer_audio(bytes(640))
            self.engine.request_flush(True)
            finished.set()

        self.engine._loop.call_soon_threadsafe(submit)
        self.assertTrue(finished.wait(1))
        deadline = time.monotonic() + 1
        while not any("audio_stream_end" in item for item in session.inputs):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)
        kinds = [next(iter(item)) for item in session.inputs]
        self.assertLess(kinds.index("audio"), kinds.index("audio_stream_end"))
        self.assertFalse(any("activity_end" in item for item in session.inputs))

    def test_speech_resuming_during_flush_does_not_release_new_turn_gate(self):
        session = self.audio_session()
        channel = self.engine._channels[True]
        started = threading.Event()
        released = threading.Event()
        original = session.send_realtime_input
        release = asyncio.Event()

        async def blocked(**kwargs):
            if "audio_stream_end" in kwargs:
                started.set()
                await release.wait()
                await original(**kwargs)
                released.set()
            else:
                await original(**kwargs)

        session.send_realtime_input = blocked
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_START))
        self.engine.request_flush(True)
        self.assertTrue(started.wait(1))
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine._loop.call_soon_threadsafe(release.set)
        self.assertTrue(released.wait(1))
        # Barrier after the sender resumes from the blocked await.
        asyncio.run_coroutine_threadsafe(asyncio.sleep(.01), self.engine._loop).result(1)
        self.assertTrue(channel.awaiting_end)

    def test_hotkey_reveals_cached_answer_without_uploading_again(self):
        self.emit(self.audio_session(), message("Already ready", True))
        self.wait_hint(True, lambda hint: hint.complete)
        captures = self.capture.call_count
        hints = list(self.engine.stream(True, lambda: False))
        self.assertEqual(hints[-1].text, "Already ready")
        self.assertEqual(self.capture.call_count, captures)
        self.assertFalse(any("audio" in item for session in self.sessions for item in session.inputs))

    def test_new_speech_invalidates_previous_answer(self):
        session = self.audio_session()
        self.emit(session, message("Old", True))
        self.wait_hint(True, lambda hint: hint.complete)
        self.emit(session, message(activity=types.VoiceActivityType.ACTIVITY_START))
        self.wait_hint(True, lambda hint: not hint.text and not hint.complete)
        self.emit(session, message(input_text="Next question"))
        self.emit(session, message(activity=types.VoiceActivityType.ACTIVITY_END))
        self.emit(session, message("New", True))
        self.wait_hint(True, lambda hint: hint.complete)
        self.assertEqual(list(self.engine.stream(True, lambda: False))[-1].text, "New")

    def test_late_input_and_output_transcripts_do_not_erase_the_answer(self):
        session = self.audio_session()
        self.emit(session, message("First", True))
        self.wait_hint(True, lambda hint: hint.complete)
        self.emit(session, message(input_text="Late final input"))
        self.emit(session, message(" tail"))
        hint = self.wait_hint(True, lambda hint: hint.text == "First tail")
        self.assertTrue(hint.complete)

    def test_late_interim_transcript_does_not_erase_completed_answer(self):
        channel = self.engine._channels[True]
        self.engine._receive_message(channel, message("Answer", True))
        self.engine._receive_message(channel, types.LiveServerMessage(server_content=types.LiveServerContent(
            interim_input_transcription=types.Transcription(text="Late interim input"))))
        self.assertEqual(channel.hint.text, "Answer")
        self.assertTrue(channel.hint.complete)

    def test_old_output_during_new_speech_is_not_shown_as_new_answer(self):
        channel = self.engine._channels[True]
        self.engine._receive_message(channel, message("Old", True))
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine._receive_message(channel, message("Old pending output", True))
        self.assertFalse(channel.hint.text)
        self.assertFalse(channel.hint.complete)
        self.engine._last_loud -= .36
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_END))
        self.engine._receive_message(channel, message("New answer", True))
        self.assertEqual(channel.hint.text, "New answer")

    def test_delayed_server_end_does_not_release_gate_for_new_local_speech(self):
        channel = self.engine._channels[True]
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_END))
        self.engine._receive_message(channel, message("Old pending response"))
        self.assertTrue(channel.awaiting_end)
        self.assertFalse(channel.hint.text)

    def test_screen_change_never_returns_cached_answer_from_previous_task(self):
        channel = self.engine._channels[False]
        self.engine._receive_message(channel, message("Old task", True))
        with self.engine._condition:
            channel.screen_stale = True
        with self.assertRaisesRegex(ConnectionError, "Экран изменился"):
            list(self.engine.stream(False, lambda: False))
        self.engine._receive_message(channel, message("Late old output"))
        self.assertEqual(channel.hint.text, "Old task")
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_START))
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_END))
        self.engine._receive_message(channel, message("New task", True))
        self.assertEqual(list(self.engine.stream(False, lambda: False))[-1].text, "New task")

    def test_meaningful_frame_change_is_detected_but_tiny_changes_are_ignored(self):
        self.assertTrue(self.engine._changed_frame(bytes(128*72), bytes([255])*(128*72)))
        tiny = bytes([255]) + bytes(128*72-1)
        self.assertFalse(self.engine._changed_frame(bytes(128*72), tiny))

    def test_speech_onset_invalidates_cached_response_before_server_transcription(self):
        session = self.audio_session()
        self.emit(session, message("Old", True))
        self.wait_hint(True, lambda hint: hint.complete)
        self.engine.offer_audio(b"\x00\x10" * 320)
        self.engine.offer_audio(b"\x00\x10" * 320)
        hint = self.engine._channels[True].hint
        self.assertFalse(hint.text)
        self.assertFalse(hint.complete)

    def test_pause_disconnects_both_sessions_and_does_not_queue_audio(self):
        self.engine.set_enabled(False).result(3)
        self.engine.offer_audio(b"secret audio")
        self.assertTrue(all(session.closed.is_set() for session in self.sessions))
        self.assertTrue(all(channel.audio.empty() for channel in self.engine._channels.values()))
        with self.assertRaisesRegex(ConnectionError, "приостановлена"):
            list(self.engine.stream(True, lambda: False))

    def test_overflow_discards_cached_answer_instead_of_building_an_unbounded_backlog(self):
        # Perform as one event-loop callback so the sender cannot drain it mid-test.
        done = threading.Event()

        def flood():
            channel = self.engine._channels[True]
            for _ in range(60):
                self.engine.offer_audio(b"pcm")
            self.assertLessEqual(channel.audio.qsize(), 50)
            self.assertFalse(channel.hint.ready)
            self.assertFalse(channel.hint.text)
            done.set()

        self.engine._loop.call_soon_threadsafe(flood)
        self.assertTrue(done.wait(1))

    def test_display_change_clears_screen_context_without_resetting_audio_only(self):
        audio = self.audio_session()
        self.emit(audio, message("Keep audio", True))
        self.wait_hint(True, lambda hint: hint.complete)
        old_screen = next(session for session in self.sessions if session is not audio)
        self.engine.set_display(9)
        self.assertTrue(old_screen.closed.wait(2))
        self.assertFalse(audio.closed.is_set())
        self.assertEqual(self.engine._channels[True].hint.text, "Keep audio")
        self.assertFalse(self.engine._channels[False].hint.text)

    def test_an_inflight_question_is_allowed_to_finish_after_the_idle_grace(self):
        channel = self.engine._channels[True]
        self.engine._receive_message(channel, message(activity=types.VoiceActivityType.ACTIVITY_START))
        session = self.audio_session()

        def answer():
            self.emit(session, message(activity=types.VoiceActivityType.ACTIVITY_END))
            self.emit(session, message("Ready after idle grace", True))

        timer = threading.Timer(1, answer)
        self.addCleanup(timer.cancel)
        timer.start()
        self.assertEqual(list(self.engine.stream(True, lambda: False))[-1].text, "Ready after idle grace")

    def test_initial_wait_times_out_even_without_any_state_changes(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            list(self.engine.stream(True, lambda: False))
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
