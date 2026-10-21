"""Experimental Q input: warm STT with an ordered keypress finalization barrier."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import threading
import time
import queue

from google.genai import types
from realtime_hints import RealtimeHints, Channel

MODEL = 'gemini-3.5-transcribe-live'
VOCABULARY = ['SQL', 'WHERE', 'HAVING', 'Python', 'async def', 'time.sleep',
              'asyncio.sleep', 'бинарный поиск']


@dataclass
class Finalize:
    pressed: float
    seconds: int
    event: threading.Event = field(default_factory=threading.Event)
    text: str = ''
    error: str = ''
    sent: bool = False
    final_seen: bool = False
    internal: bool = False


class TranscriptionChannel(Channel):
    def __init__(self):
        super().__init__()
        self.reset_audio()

    def reset_audio(self):
        # Cover the 1.2s finalization hold and two 2s transport deadlines.
        self.audio = queue.Queue(maxsize=300)  # At most six seconds, 192 KB PCM.


class LiveTranscription(RealtimeHints):
    def __init__(self, keys, on_status=None):
        self._segments = deque(maxlen=200)
        self._pending = None
        super().__init__(keys, MODEL, '', on_status, channel=TranscriptionChannel(), config=types.LiveConnectConfig(
            response_modalities=['TEXT'], input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=['ru-RU'], custom_vocabulary=VOCABULARY),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True))))

    def _begin_turn(self, channel):
        # STT has no answers to invalidate when someone starts talking again.
        channel.receiving = True

    def _invalidate(self, channel, error=''):
        with self._condition:
            self._segments.clear()
            if self._pending:
                self._pending.error = error or 'Распознавание переподключается'
                self._pending.event.set()
                self._pending = None
            super()._invalidate(channel, error)

    async def _sender(self, channel, session, generation):
        await asyncio.wait_for(session.send_realtime_input(activity_start=types.ActivityStart()), 2)
        window_started = time.monotonic()
        window_has_signal = False
        while self._current(generation):
            try:
                captured, item = await asyncio.to_thread(channel.audio.get, True, .1)
            except queue.Empty:
                continue
            if not self._current(generation):
                return
            if not channel.hint.ready or time.monotonic() - captured > 6:
                raise ConnectionError('Очередь распознавания отстала; переподключаюсь')
            if isinstance(item, Finalize):
                item.sent = True
                await asyncio.wait_for(session.send_realtime_input(activity_end=types.ActivityEnd()), 2)
                # Later audio stays local until this question is finalized. It must
                # not enter the transcript frozen for the current keypress.
                if not await asyncio.to_thread(item.event.wait, 1.2):
                    raise TimeoutError('Распознавание не завершило вопрос вовремя')
                if item.error:
                    raise ConnectionError(item.error)
                await asyncio.wait_for(session.send_realtime_input(activity_start=types.ActivityStart()), 2)
                window_started = time.monotonic()
                window_has_signal = False
            else:
                await asyncio.wait_for(session.send_realtime_input(audio=types.Blob(
                    data=item, mime_type='audio/pcm;rate=16000')), 2)
                window_has_signal = window_has_signal or any(item)
                # Bound the size of a manual turn even if Q has not been pressed.
                # These markers use the same FIFO; only one end may be outstanding.
                if time.monotonic() - window_started >= 30:
                    with self._condition:
                        if self._pending is None and window_has_signal:
                            ticket = Finalize(time.monotonic(), 60, internal=True)
                            channel.audio.put_nowait((ticket.pressed, ticket))
                            self._pending = ticket
                        elif not window_has_signal:
                            window_started = time.monotonic()

    def _receive_message(self, channel, message):
        with self._condition:
            if message.go_away:
                raise ConnectionError('Распознавание обновляет сессию')
            content = message.server_content
            if not content:
                return
            if content.input_transcription and content.input_transcription.text:
                self._segments.append((time.monotonic(), content.input_transcription.text))
                if self._pending and self._pending.sent:
                    self._pending.final_seen = True
            if content.generation_complete and self._pending and self._pending.sent:
                ticket = self._pending
                ticket.text = (' '.join(text for received, text in self._segments
                                       if received >= ticket.pressed - ticket.seconds).strip()
                               if ticket.final_seen else '')
                if not ticket.text and not ticket.internal:
                    ticket.error = 'Вопрос не распознан'
                if not ticket.internal:
                    self._segments.clear()  # Never repeat the previous button's question.
                self._pending = None
                ticket.event.set()

    def question(self, seconds, cancelled):
        with self._condition:
            if not self.enabled or not self._channel.hint.ready:
                raise ConnectionError('Поток распознавания не подключён или на паузе')
            if self._pending is not None:
                raise ConnectionError('Предыдущая финализация ещё не завершена')
            ticket = Finalize(time.monotonic(), seconds)
            # Same lock as offer_audio: this marker follows every earlier PCM frame.
            self._channel.audio.put_nowait((ticket.pressed, ticket))
            self._pending = ticket
        deadline = time.monotonic() + 1.5
        while not cancelled():
            if ticket.event.wait(.02):
                if ticket.error:
                    raise ConnectionError(ticket.error)
                return ticket.text
            if time.monotonic() > deadline:
                raise TimeoutError('Распознавание не завершило вопрос вовремя')
        raise InterruptedError('Запрос отменён')

    def status(self):
        with self._condition:
            if not self.enabled:
                return 'Тест ⌥Q: распознавание на паузе'
            hint = self._channel.hint
            state = 'подключено' if hint.ready else hint.error[:90] or 'подключается'
            return f'Тест ⌥Q: распознавание + Flash-Lite · {state}'
