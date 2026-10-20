import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from live_connection import LiveConnection


def message(text=None, complete=False):
    return SimpleNamespace(
        go_away=None,
        server_content=SimpleNamespace(
            output_transcription=SimpleNamespace(text=text) if text else None,
            interrupted=False,
            turn_complete=complete,
        ),
    )


async def responses(*messages):
    for item in messages:
        yield item


class LiveConnectionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.session = Mock()
        turns = asyncio.Queue()
        self.turns = turns
        self.response_stream = Mock()

        async def send(**kwargs):
            if "activity_end" in kwargs:
                turns.put_nowait(None)

        async def receive():
            await turns.get()
            async for response in self.response_stream():
                yield response

        self.session.send_realtime_input = AsyncMock(side_effect=send)
        self.session.receive = receive
        self.context = Mock()
        self.connected = threading.Event()
        self.disconnected = threading.Event()

        async def enter():
            self.connected.set()
            return self.session

        async def exit_context(*args):
            self.disconnected.set()

        self.context.__aenter__ = AsyncMock(side_effect=enter)
        self.context.__aexit__ = AsyncMock(side_effect=exit_context)
        self.client.aio.live.connect.return_value = self.context
        self.connection = LiveConnection(self.client, "gemini-3.8-live", "Give a short answer")
        self.addCleanup(self.connection.close)
        self.assertTrue(self.connected.wait(timeout=1), "Session should connect before a request")

    def test_preconnected_session_is_reused_and_receives_raw_pcm(self):
        self.response_stream.side_effect = [responses(message("First", True)), responses(message("Second", True))]
        pcm = b"\x01\x02" * 4500
        self.assertEqual(list(self.connection.stream(b"jpeg", pcm, lambda: False)), ["First"])
        self.assertEqual(list(self.connection.stream(b"jpeg2", b"pcm2", lambda: False)), ["Second"])
        self.client.aio.live.connect.assert_called_once()
        calls = self.session.send_realtime_input.call_args_list
        self.assertIn("activity_start", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["video"].data, b"jpeg")
        audio = [call.kwargs["audio"].data for call in calls[:6] if "audio" in call.kwargs]
        self.assertEqual(b"".join(audio), pcm)
        self.assertIn("activity_end", calls[5].kwargs)
        config = self.client.aio.live.connect.call_args.kwargs["config"]
        self.assertTrue(config.realtime_input_config.automatic_activity_detection.disabled)
        self.assertIsNone(config.thinking_config)
        self.assertIsNotNone(config.output_audio_transcription)

    def test_cancel_closes_session_while_waiting_for_more_text(self):
        receive_closed = threading.Event()

        async def stalled():
            try:
                yield message("First")
                await asyncio.Event().wait()
            finally:
                receive_closed.set()

        self.response_stream.side_effect = stalled
        cancelled = threading.Event()
        stream = self.connection.stream(b"jpeg", b"pcm", cancelled.is_set)
        self.assertEqual(next(stream), "First")
        cancelled.set()
        self.assertEqual(list(stream), [])
        self.assertTrue(self.disconnected.wait(timeout=1))
        self.assertTrue(receive_closed.wait(timeout=1))

    def test_expired_session_reports_error_and_preconnects_again(self):
        self.response_stream.side_effect = [
            responses(SimpleNamespace(go_away=True)),
            responses(message("Recovered", True)),
        ]
        self.connected.clear()
        with self.assertRaisesRegex(ConnectionError, "expiring"):
            list(self.connection.stream(b"jpeg", b"pcm", lambda: False))
        self.assertTrue(self.connected.wait(timeout=1))
        self.assertEqual(list(self.connection.stream(b"jpeg", b"pcm", lambda: False)), ["Recovered"])
        self.assertEqual(self.client.aio.live.connect.call_count, 2)

    def test_audio_without_transcript_is_an_error(self):
        self.response_stream.return_value = responses(message(complete=True))
        with self.assertRaisesRegex(ConnectionError, "no complete text"):
            list(self.connection.stream(b"jpeg", b"pcm", lambda: False))

    def test_reset_drops_old_context_and_opens_fresh_session(self):
        self.connected.clear()
        self.connection.reset()
        self.assertTrue(self.connected.wait(timeout=1))
        self.assertTrue(self.disconnected.is_set())
        self.assertEqual(self.client.aio.live.connect.call_count, 2)

    def test_reset_without_an_eligible_key_disconnects_without_reconnecting(self):
        self.connection.reset(None).result(timeout=2)
        self.assertTrue(self.disconnected.is_set())
        self.client.aio.live.connect.assert_called_once()

    def test_switching_key_closes_previous_session(self):
        replacement = Mock()
        replacement.aio.live.connect.return_value = self.context
        self.connection.use_client(replacement)
        # Wait for warmup queued by the client switch.
        asyncio.run_coroutine_threadsafe(self.connection._warm(), self.connection._loop).result(timeout=2)
        self.assertTrue(self.disconnected.is_set())
        replacement.aio.live.connect.assert_called_once()

    def test_idle_expiration_reconnects_before_the_next_hotkey(self):
        self.response_stream.return_value = responses(SimpleNamespace(go_away=True))
        self.connected.clear()
        self.connection._loop.call_soon_threadsafe(self.turns.put_nowait, None)
        self.assertTrue(self.connected.wait(timeout=1))
        self.session.send_realtime_input.assert_not_called()
        self.assertEqual(self.client.aio.live.connect.call_count, 2)

    def test_idle_connection_ending_without_messages_reconnects(self):
        self.response_stream.return_value = responses()
        self.connected.clear()
        self.connection._loop.call_soon_threadsafe(self.turns.put_nowait, None)
        self.assertTrue(self.connected.wait(timeout=1))
        self.assertEqual(self.client.aio.live.connect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
