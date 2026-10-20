"""Continuous Live input, with isolated audio-only and screen-aware contexts."""

import asyncio
import io
from array import array
import queue
import threading
import time
from dataclasses import dataclass, replace

from google.genai import types
from PIL import Image

from api_keys import credential_error


@dataclass(frozen=True)
class Hint:
    revision: int = 0
    turn: int = 0
    text: str = ""
    first_text_at: float = 0
    updated_at: float = 0
    complete: bool = False
    ready: bool = False
    error: str = ""
    vad_end_at: float = 0


class Channel:
    def __init__(self, audio_only):
        self.audio_only = audio_only
        self.audio = queue.Queue(maxsize=50)  # At most one second, never an ever-growing upload.
        self.hint = Hint()
        self.receiving = False
        self.screen_stale = False
        self.awaiting_end = False
        self.server_speech = False
        self.flushed_turn = -1

    def drain(self):
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                return


class RealtimeHints:
    def __init__(self, keys, model, instruction, capture, display_id, on_status=None):
        self.keys, self.model, self.capture = keys, model, capture
        self._display_id = display_id
        self._display_version = 0
        self._frame = None
        self._frame_signature = None
        self._voice_active = False
        self._last_loud = 0
        self._voiced_frames = 0
        self._frame_error = "Экран ещё не подготовлен"
        self._on_status = on_status
        self._condition = threading.Condition()
        self._channels = {True: Channel(True), False: Channel(False)}
        self.enabled = True
        self._closed = False
        self._generation = 0
        self._tasks = []
        self._config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
            system_instruction=instruction,
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False, prefix_padding_ms=20, silence_duration_ms=350,
                ),
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
        )
        # Gemini 3.8 Live rejects thinking_level; 3.1 uses levels, 2.5 uses a budget.
        if "3.1" in model:
            self._config.thinking_config = types.ThinkingConfig(thinking_level="minimal")
        elif "2.5" in model:
            self._config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        self._lifecycle = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="realtime-hints")
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._restart(), self._loop)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    def _publish(self, channel, **values):
        with self._condition:
            channel.hint = replace(channel.hint, revision=channel.hint.revision + 1, **values)
            self._condition.notify_all()
        if self._on_status and any(key in values for key in ("ready", "error")):
            self._on_status()

    def _invalidate(self, channel, error=""):
        channel.receiving = False
        channel.screen_stale = False
        channel.awaiting_end = False
        channel.server_speech = False
        self._publish(channel, turn=channel.hint.turn + 1, text="", first_text_at=0,
                      updated_at=0, complete=False, ready=False, error=error, vad_end_at=0)

    def _begin_turn(self, channel):
        channel.receiving = True
        channel.awaiting_end = True
        channel.screen_stale = False
        self._publish(channel, turn=channel.hint.turn + 1, text="", first_text_at=0,
                      updated_at=0, complete=False, vad_end_at=0)

    def offer_audio(self, pcm):
        # PortAudio callback: bounded in-memory work only; never wait for the network.
        with self._condition:
            if not self.enabled or self._closed:
                return
            now = time.monotonic()
            # This is only early cache invalidation. The server VAD still owns turn endings.
            samples = array("h", pcm) if len(pcm) % 2 == 0 else ()
            loud = bool(samples) and sum(value * value for value in samples) / len(samples) > 250 ** 2
            if loud:
                self._last_loud = now
                self._voiced_frames += 1
                if not self._voice_active and self._voiced_frames >= 2:
                    self._voice_active = True
                    for channel in self._channels.values():
                        if channel.hint.ready:
                            self._begin_turn(channel)
            else:
                self._voiced_frames = 0
                if self._voice_active and now - self._last_loud > .35:
                    self._voice_active = False
                    for channel in self._channels.values():
                        if not channel.server_speech:
                            channel.awaiting_end = False
            for channel in self._channels.values():
                if not channel.hint.ready:
                    continue
                try:
                    channel.audio.put_nowait((now, pcm))
                except queue.Full:
                    self._invalidate(channel, "Сеть не успевает передавать звук; переподключаюсь")

    def set_display(self, display_id):
        with self._condition:
            if display_id == self._display_id:
                return
            self._display_id = display_id
            self._display_version += 1
            self._frame = None
            self._frame_signature = None
            self._frame_error = "Экран изменён; ожидаю свежий снимок"
            self._invalidate(self._channels[False], self._frame_error)
        # Do not retain answers or frames from the previously selected display.
        asyncio.run_coroutine_threadsafe(self._restart_screen(), self._loop)

    async def _restart_screen(self):
        async with self._lifecycle:
            if len(self._tasks) >= 3:
                self._tasks[2].cancel()
                await asyncio.gather(self._tasks[2], return_exceptions=True)
                if self.enabled and not self._closed:
                    self._tasks[2] = asyncio.create_task(self._channel_loop(self._channels[False], self._generation))
    def set_enabled(self, enabled):
        with self._condition:
            self.enabled = bool(enabled)
            self._generation += 1
            self._frame = None
            self._frame_signature = None
            self._voice_active = False
            for channel in self._channels.values():
                channel.drain()
                self._invalidate(channel)
        return asyncio.run_coroutine_threadsafe(self._restart(), self._loop)

    def reset(self):
        return self.set_enabled(self.enabled)

    async def _restart(self):
        async with self._lifecycle:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []
            if self.enabled and not self._closed:
                generation = self._generation
                self._tasks = [asyncio.create_task(self._capture_loop(generation)),
                               asyncio.create_task(self._channel_loop(self._channels[True], generation)),
                               asyncio.create_task(self._channel_loop(self._channels[False], generation))]
    def _current(self, generation):
        return self.enabled and not self._closed and generation == self._generation

    @staticmethod
    def _signature(jpeg):
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                # Ignore the menu-bar clock; compare a small visual fingerprint.
                image = image.crop((0, int(image.height * .04), image.width, image.height))
                return image.convert("L").resize((128, 72)).tobytes()
        except (OSError, ValueError):
            return jpeg

    @staticmethod
    def _changed_frame(previous, current):
        if previous is None:
            return False
        if len(previous) != len(current):
            return True
        # Ignore tiny cursor/caret/antialiasing changes, invalidate actual content changes.
        changed = sum(abs(a - b) > 12 for a, b in zip(previous, current))
        return changed > max(1, len(current) * .01)

    async def _capture_loop(self, generation):
        while self._current(generation):
            if not self.keys.candidates(self.model):
                await asyncio.sleep(1.5)
                continue
            with self._condition:
                display_id, version = self._display_id, self._display_version
            started = time.monotonic()
            try:
                frame = await asyncio.to_thread(self.capture, display_id)
                signature = self._signature(frame)
                with self._condition:
                    if self._current(generation) and version == self._display_version:
                        if self._changed_frame(self._frame_signature, signature):
                            channel = self._channels[False]
                            channel.screen_stale = True
                            self._publish(channel, text="", first_text_at=0, updated_at=0, complete=False,
                                          turn=channel.hint.turn + 1)
                        self._frame_signature = signature
                        self._frame = (version, time.monotonic(), frame)
                        self._frame_error = ""
                print(f"[realtime] capture={time.monotonic()-started:.3f}s jpeg={len(frame)} bytes")
            except Exception as error:
                with self._condition:
                    if version == self._display_version:
                        self._frame = None
                        self._frame_error = self.keys.redact(error)
            await asyncio.sleep(max(.05, 1.5 - (time.monotonic() - started)))

    def _fresh_frame(self):
        with self._condition:
            frame = self._frame
            if frame is None or time.monotonic() - frame[1] > 3:
                raise ConnectionError(self._frame_error or "Нет свежего снимка выбранного экрана")
            return frame

    async def _sender(self, channel, session, send_lock, generation):
        while self._current(generation):
            try:
                captured, pcm = await asyncio.to_thread(channel.audio.get, True, .1)
            except queue.Empty:
                continue
            if not channel.hint.ready or time.monotonic() - captured > 1:
                raise ConnectionError("Накопилась очередь аудио; начинаю свежий поток")
            async with send_lock:
                if not self._current(generation):
                    return
                if pcm is None:
                    with self._condition:
                        if self._last_loud > captured:
                            continue  # Speech resumed after this keypress; don't cut it off.
                        flushed_turn = channel.hint.turn
                    await asyncio.wait_for(session.send_realtime_input(audio_stream_end=True), timeout=2)
                    with self._condition:
                        if (self._current(generation) and channel.hint.turn == flushed_turn
                                and self._last_loud <= captured):
                            channel.awaiting_end = False
                else:
                    await asyncio.wait_for(session.send_realtime_input(audio=types.Blob(
                        data=pcm, mime_type="audio/pcm;rate=16000")), timeout=2)

    async def _frames(self, session, send_lock, generation, sent=None):
        while self._current(generation):
            frame = self._fresh_frame()
            if frame[:2] != sent:
                async with send_lock:
                    if not self._current(generation) or frame[0] != self._display_version:
                        return
                    await asyncio.wait_for(session.send_realtime_input(
                        video=types.Blob(data=frame[2], mime_type="image/jpeg")), timeout=2)
                sent = frame[:2]
            await asyncio.sleep(.1)

    def _receive_message(self, channel, message):
        with self._condition:
            now = time.monotonic()
            if message.go_away:
                raise ConnectionError("Live обновляет сессию")
            activity = getattr(message, "voice_activity", None)
            if activity and activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_START:
                channel.server_speech = True
                if not channel.receiving or channel.screen_stale or channel.hint.complete:
                    self._begin_turn(channel)
            elif activity and activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_END:
                channel.server_speech = False
                # Server events may lag behind newer local speech.
                if now - self._last_loud >= .35:
                    channel.awaiting_end = False
                self._publish(channel, vad_end_at=now)
            content = message.server_content
            if not content:
                return
            # Input/interim transcripts are independent of output ordering. They
            # cannot identify a new turn; local speech and server VAD own boundaries.
            if content.interrupted:
                channel.receiving = channel.awaiting_end
                self._publish(channel, turn=channel.hint.turn + 1, text="", first_text_at=0, complete=False)
            if (content.output_transcription and content.output_transcription.text
                    and not channel.screen_stale and not channel.awaiting_end):
                channel.receiving = True
                self._publish(channel, text=channel.hint.text + content.output_transcription.text,
                              first_text_at=channel.hint.first_text_at or now, updated_at=now, error="")
            if (content.turn_complete or content.generation_complete) and not channel.awaiting_end:
                channel.receiving = False
                self._publish(channel, complete=True)

    async def _receiver(self, channel, session):
        while True:
            received = False
            async for message in session.receive():
                received = True
                self._receive_message(channel, message)
            if not received:
                raise ConnectionError("Live закрыл поток")

    async def _session(self, channel, entry, generation):
        if not channel.audio_only:
            self._fresh_frame()
        client = self.keys.client(entry)
        context = client.aio.live.connect(model=self.model, config=self._config)
        started = time.monotonic()
        session = await asyncio.wait_for(context.__aenter__(), timeout=5)
        tasks = []
        try:
            sent_frame = None
            if not channel.audio_only:
                frame = self._fresh_frame()
                await asyncio.wait_for(session.send_realtime_input(
                    video=types.Blob(data=frame[2], mime_type="image/jpeg")), timeout=2)
                sent_frame = frame[:2]
            channel.drain()
            self.keys.succeeded(entry)
            self._publish(channel, ready=True, error="")
            print(f"[realtime {'audio' if channel.audio_only else 'screen'}] connected={time.monotonic()-started:.3f}s")
            lock = asyncio.Lock()
            tasks = [asyncio.create_task(self._sender(channel, session, lock, generation)),
                     asyncio.create_task(self._receiver(channel, session))]
            if not channel.audio_only:
                tasks.append(asyncio.create_task(self._frames(session, lock, generation, sent_frame)))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._invalidate(channel)
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=2)

    async def _channel_loop(self, channel, generation):
        backoff = 1
        while self._current(generation):
            candidates = self.keys.candidates(self.model)
            if not candidates:
                self._invalidate(channel, self.keys.unavailable_message())
                await asyncio.sleep(1)
                continue
            entry = candidates[0]
            try:
                await self._session(channel, entry, generation)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if credential_error(error):
                    self.keys.failed(entry, self.model, error)
                self._invalidate(channel, self.keys.redact(error))
                print(f"[realtime] {self.keys.redact(error)}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)

    def request_flush(self, audio_only):
        """A keypress can finalize the quiet tail without waiting the full server VAD."""
        with self._condition:
            channel = self._channels[audio_only]
            now = time.monotonic()
            if (not self.enabled or not channel.hint.ready or channel.hint.text
                    or not channel.receiving or now - self._last_loud < .06
                    or channel.flushed_turn == channel.hint.turn):
                return
            try:
                # Ordered AFTER the audio already captured, through the same sender.
                channel.audio.put_nowait((now, None))
                channel.flushed_turn = channel.hint.turn
            except queue.Full:
                return  # The sender's normal overflow/staleness handling takes over.

    def stream(self, audio_only, cancelled):
        """Reveal the latest answer; never concatenate separate spoken turns."""
        channel = self._channels[audio_only]
        self.request_flush(audio_only)
        # Give an actual in-flight question time to finish. An idle/empty stream
        # gets only a short grace period before the on-demand fallback.
        deadline = time.monotonic() + (2.5 if channel.receiving else .8)
        revision = -1
        turn = None
        has_text = False
        while not cancelled():
            if time.monotonic() > deadline:
                raise TimeoutError("Поток ещё не подготовил свежий ответ")
            with self._condition:
                hint = channel.hint
                if not self.enabled or self._closed:
                    raise ConnectionError("Фоновая передача приостановлена")
                if not audio_only:
                    self._fresh_frame()
                if channel.screen_stale:
                    raise ConnectionError("Экран изменился; нужен свежий запрос")
                if not hint.ready:
                    raise ConnectionError(hint.error or "Фоновый поток ещё подключается")
                if hint.error:
                    raise ConnectionError(hint.error)
                if hint.revision == revision:
                    self._condition.wait(timeout=.02)
                    continue
                revision = hint.revision
            if has_text and hint.turn != turn:
                raise ConnectionError("Начался новый вопрос; предыдущая подсказка остановлена")
            if hint.text and time.monotonic() - hint.updated_at < 20:
                if not has_text:
                    turn = hint.turn
                    deadline = time.monotonic() + 40
                has_text = True
                yield hint
                if hint.complete:
                    return
            if time.monotonic() > deadline:
                raise TimeoutError("Поток ещё не подготовил свежий ответ")

    def status(self):
        with self._condition:
            if not self.enabled:
                return "Поток: пауза · звук и экран не отправляются в фоне"
            states = [f"{'звук' if audio else 'экран'} {'готов' if channel.hint.ready else channel.hint.error[:60] or 'подключается'}"
                      for audio, channel in self._channels.items()]
            return "Поток в Gemini: " + " · ".join(states)

    def close(self):
        with self._condition:
            self._closed = True
            self.enabled = False
            self._generation += 1
            self._condition.notify_all()
        future = asyncio.run_coroutine_threadsafe(self._restart(), self._loop)
        try:
            future.result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
