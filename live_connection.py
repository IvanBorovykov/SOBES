"""A preconnected Live session; microphone data is sent only on a hotkey request."""

import asyncio
import queue
import threading
import time
from contextlib import aclosing

from google.genai import types

from api_keys import credential_error


_KEEP_CLIENT = object()


class LiveConnection:
    def __init__(self, client, model, instruction, redact=str, on_key_error=None):
        self._client = client
        self._model = model
        self._redact = redact
        self._on_key_error = on_key_error
        self._config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=instruction,
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
            ),
        )
        # The default 3.8 Live model does not accept thinking_level in setup.
        self._loop = asyncio.new_event_loop()
        self._connect_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._session = None
        self._audio_only = None
        self._context = None
        self._reader = None
        self._messages = None
        self._active = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if client is not None:
            self._warmup = asyncio.run_coroutine_threadsafe(self._warm(), self._loop)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    async def _connect(self):
        async with self._connect_lock:
            if self._reader is not None and self._reader.done():
                await self._close_session()
            if self._session is None:
                if self._client is None:
                    raise ValueError("Добавьте API-ключ в настройках.")
                context = self._client.aio.live.connect(model=self._model, config=self._config)
                session = await asyncio.wait_for(context.__aenter__(), timeout=5)
                self._context, self._session = context, session
                self._messages = asyncio.Queue()
                self._reader = asyncio.create_task(self._read(session, self._messages))
            return self._session

    async def _read(self, session, messages):
        try:
            while True:
                received = False
                async with aclosing(session.receive()) as responses:
                    async for message in responses:
                        received = True
                        if message.go_away:
                            raise ConnectionError("Live session is expiring")
                        if self._active is not None:
                            messages.put_nowait(message)
                if not received:
                    raise ConnectionError("Live connection closed")
        except Exception as error:
            messages.put_nowait(error)
            if credential_error(error) and self._on_key_error:
                self._on_key_error(self._client, self._model, error)
            # Process disconnect notices even when the user isn't requesting a turn.
            if self._active is None and not credential_error(error):
                asyncio.create_task(self._warm())

    async def _warm(self):
        if self._client is None:
            return
        try:
            await self._connect()
            print(f"[{self._model}] Live connection ready")
        except Exception as error:
            if credential_error(error) and self._on_key_error:
                self._on_key_error(self._client, self._model, error)
            print(f"[{self._model}] Live connection unavailable: {self._redact(error)}")

    async def _set_client(self, client):
        async with self._turn_lock:
            if self._client is client:
                return
            await self._disconnect()
            self._client = client
            if client is not None:
                asyncio.create_task(self._warm())

    def set_client(self, client):
        return asyncio.run_coroutine_threadsafe(self._set_client(client), self._loop)

    def use_client(self, client):
        self.set_client(client).result(timeout=12)

    async def _disconnect(self):
        async with self._connect_lock:
            await self._close_session()

    async def _close_session(self):
        context, self._context = self._context, None
        reader, self._reader = self._reader, None
        self._session = None
        self._audio_only = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if context is not None:
            await context.__aexit__(None, None, None)

    async def _turn(self, jpeg, pcm, output):
        async with self._turn_lock:
            self._active = asyncio.current_task()
            try:
                async with asyncio.timeout(45):
                    audio_only = jpeg is None
                    if self._audio_only is not None and self._audio_only != audio_only:
                        await self._disconnect()
                    session = await self._connect()
                    self._audio_only = audio_only
                    messages = self._messages
                    # Explicit activity boundaries avoid waiting for a VAD silence timer.
                    await session.send_realtime_input(activity_start=types.ActivityStart())
                    if jpeg is not None:
                        await session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
                    for offset in range(0, len(pcm), 4096):
                        await session.send_realtime_input(audio=types.Blob(
                            data=pcm[offset:offset + 4096], mime_type="audio/pcm;rate=16000",
                        ))
                    await session.send_realtime_input(activity_end=types.ActivityEnd())

                    first_deadline = time.monotonic() + 5
                    total_deadline = time.monotonic() + 45
                    has_text = False
                    complete = False
                    while not complete:
                        deadline = total_deadline if has_text else first_deadline
                        message = await asyncio.wait_for(messages.get(), timeout=max(0, deadline - time.monotonic()))
                        if isinstance(message, Exception):
                            raise message
                        content = message.server_content
                        if not content:
                            continue
                        # Do not play model audio or show its internal thought parts.
                        if content.output_transcription and content.output_transcription.text:
                            output.put(content.output_transcription.text)
                            has_text = True
                        if content.interrupted:
                            raise ConnectionError("Live response interrupted")
                        complete = bool(content.turn_complete)
                    if not complete or not has_text:
                        raise ConnectionError("Live returned no complete text response")
            except (Exception, asyncio.CancelledError) as error:
                await self._disconnect()
                if isinstance(error, asyncio.CancelledError):
                    raise
                output.put(error)
                # Reopen ahead of the next hotkey, without replaying user input.
                if credential_error(error):
                    if self._on_key_error:
                        self._on_key_error(self._client, self._model, error)
                else:
                    asyncio.create_task(self._warm())
            finally:
                self._active = None
                output.put(None)

    def stream(self, jpeg, pcm, cancelled):
        output = queue.Queue()
        future = asyncio.run_coroutine_threadsafe(self._turn(jpeg, pcm, output), self._loop)
        try:
            while not cancelled():
                try:
                    item = output.get(timeout=0.05)
                except queue.Empty:
                    if future.done():
                        future.result()
                        return
                    continue
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                if not cancelled():
                    yield item
        finally:
            if not future.done():
                future.cancel()

    async def _reset(self, client=_KEEP_CLIENT):
        if self._active:
            self._active.cancel()
            await asyncio.gather(self._active, return_exceptions=True)
        async with self._turn_lock:
            await self._disconnect()
            if client is not _KEEP_CLIENT:
                self._client = client
            await self._warm()

    def reset(self, client=_KEEP_CLIENT):
        return asyncio.run_coroutine_threadsafe(self._reset(client), self._loop)

    async def _shutdown(self):
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._disconnect()

    def close(self):
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=1)
