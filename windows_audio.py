"""WASAPI loopback capture at the output's native rate -> 16 kHz mono PCM16."""

import array
import audioop
import threading

import pyaudiowpatch as pyaudio


class PcmConverter:
    def __init__(self, rate, channels):
        self.rate, self.channels = rate, channels
        self.state = None
        self.pending = bytearray()

    def feed(self, data):
        if self.channels == 2:
            data = audioop.tomono(data, 2, .5, .5)
        elif self.channels > 2:
            samples = array.array('h', data)
            data = array.array('h', (round(sum(samples[i:i + self.channels]) / self.channels)
                                    for i in range(0, len(samples), self.channels))).tobytes()
        if self.rate != 16000:
            data, self.state = audioop.ratecv(data, 2, 1, self.rate, 16000, self.state)
        self.pending.extend(data)
        while len(self.pending) >= 640:
            frame = bytes(self.pending[:640])
            del self.pending[:640]
            yield frame


class WindowsSystemAudio:
    def __init__(self, on_audio, on_error, on_ready=lambda: None):
        self._stop = threading.Event()
        self._on_audio, self._on_error, self._on_ready = on_audio, on_error, on_ready
        self._worker = threading.Thread(target=self._run, daemon=True, name='wasapi-loopback')
        self._worker.start()

    def _run(self):
        try:
            with pyaudio.PyAudio() as pa:
                device = pa.get_default_wasapi_loopback()
                rate, channels = int(device['defaultSampleRate']), int(device['maxInputChannels'])
                if channels < 1:
                    raise ValueError('Устройство вывода не поддерживает loopback.')
                converter = PcmConverter(rate, channels)
                with pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                             input_device_index=device['index'], frames_per_buffer=max(1, rate // 50)) as stream:
                    if self._stop.is_set():
                        return
                    self._on_ready()
                    while not self._stop.is_set():
                        data = stream.read(max(1, rate // 50), exception_on_overflow=True)
                        for frame in converter.feed(data):
                            if not self._stop.is_set():
                                self._on_audio(frame)
        except (OSError, ValueError, LookupError) as error:
            if not self._stop.is_set():
                self._on_error(f'Не удалось захватить звук Windows: {error}. '
                               'Проверьте устройство вывода и повторно нажмите «Применить».')

    def close(self):
        self._stop.set()
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=2)
