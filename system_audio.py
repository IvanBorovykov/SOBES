"""Native macOS system audio -> bounded 20 ms PCM callbacks, without screen frames."""
import subprocess
import tempfile
import threading
from pathlib import Path


class SystemAudio:
    def __init__(self, on_audio, on_error, on_ready=lambda: None):
        self._stopped = False
        self._process = None
        self._state_lock = threading.Lock()
        self._on_audio, self._on_error, self._on_ready = on_audio, on_error, on_ready
        self._reader = None
        self._worker = threading.Thread(target=self._start, daemon=True, name='system-audio-start')
        self._worker.start()

    def _launch(self, args):
        with self._state_lock:
            if self._stopped:
                return None
            self._process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            return self._process

    def _start(self):
        try:
            source = Path(__file__).resolve().parent / 'native/system_audio.swift'
            binary = source.parent.parent / '__pycache__/system_audio'
            binary.parent.mkdir(exist_ok=True)
            if not binary.exists() or binary.stat().st_mtime_ns < source.stat().st_mtime_ns:
                with tempfile.TemporaryDirectory(prefix='audio-build-', dir=binary.parent) as directory:
                    built = Path(directory) / 'system_audio'
                    compiler = self._launch(['xcrun', 'swiftc', '-O', str(source), '-o', str(built)])
                    if compiler is None:
                        return
                    try:
                        _, error = compiler.communicate(timeout=60)
                    except subprocess.TimeoutExpired:
                        compiler.kill()
                        compiler.communicate()
                        raise ValueError('Сборка захвата звука macOS превысила 60 секунд')
                    if compiler.returncode:
                        raise ValueError('Не удалось собрать захват звука macOS: ' + error.decode(errors='replace')[-1500:])
                    with self._state_lock:
                        if self._stopped:
                            return
                        built.replace(binary)
            process = self._launch([str(binary)])
            if process is None:
                return
            self._reader = threading.Thread(target=self._read, daemon=True, name='system-audio')
            self._reader.start()
            self._read_errors()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if not self._stopped:
                self._on_error(str(error))

    def _read(self):
        pending = bytearray()
        try:
            while not self._stopped:
                data = self._process.stdout.read(640 - len(pending))
                if not data:
                    break
                pending.extend(data)
                if len(pending) == 640:
                    self._on_audio(bytes(pending))
                    pending.clear()
        finally:
            self._process.stdout.close()

    def _read_errors(self):
        try:
            reported = False
            for line in self._process.stderr:
                if self._stopped:
                    return
                text = line.decode('utf-8', errors='replace').strip()
                if text == 'READY':
                    self._on_ready()
                else:
                    reported = True
                    self._on_error(text)
            code = self._process.wait()
            if not self._stopped and not reported:
                self._on_error(f'Захват системного аудио остановлен (код {code}). Проверьте разрешение macOS и повторно выберите источник в настройках.')
        finally:
            self._process.stderr.close()

    def close(self):
        with self._state_lock:
            self._stopped = True
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._worker.join(timeout=2)
        if self._reader:
            self._reader.join(timeout=2)
