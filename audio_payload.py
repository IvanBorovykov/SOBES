"""Small inline speech payloads; continuous Live input stays raw PCM."""

import io
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
FRAME_BYTES = 320 * SAMPLE_WIDTH  # 20 ms


@dataclass(frozen=True)
class AudioPayload:
    data: bytes = field(repr=False)
    mime_type: str
    original_seconds: float
    sent_seconds: float
    prepare_seconds: float


def trim_silence(pcm):
    """Trim digital silence only; keep 300 ms padding and every interior pause."""
    if len(pcm) % SAMPLE_WIDTH:
        raise ValueError("Неполный PCM-сэмпл в аудиобуфере.")
    if not pcm or not any(pcm):
        return b""
    active = []
    for offset in range(0, len(pcm), FRAME_BYTES):
        if any(pcm[offset:offset + FRAME_BYTES]):
            active.append(offset)
    if not active:
        return pcm  # Quiet speech is safer to keep than to classify as silence.
    padding = 15 * FRAME_BYTES
    start = max(0, active[0] - padding)
    end = min(len(pcm), active[-1] + FRAME_BYTES + padding)
    # Do not pay the cost of editing very short quiet tails.
    if start < 5 * FRAME_BYTES:
        start = 0
    if len(pcm) - end < 5 * FRAME_BYTES:
        end = len(pcm)
    return pcm[start:end]


def _ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).is_file():
            return candidate
    return None


def prepare_audio(pcm):
    started = time.perf_counter()
    trimmed = trim_silence(pcm)
    if not trimmed:
        return None
    original_seconds = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)
    seconds = len(trimmed) / (SAMPLE_RATE * SAMPLE_WIDTH)
    executable = _ffmpeg() if seconds >= 1 else None
    if executable:
        try:
            result = subprocess.run([
                executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
                "-map_metadata", "-1", "-c:a", "libopus", "-application", "voip",
                "-b:a", "24k", "-vbr", "on", "-compression_level", "0",
                "-frame_duration", "20", "-threads", "1",
                "-f", "ogg", "pipe:1",
            ], input=trimmed, capture_output=True, check=True, timeout=.5)
            encoded = result.stdout
            if (encoded.startswith(b"OggS") and b"OpusHead" in encoded[:128]
                    and len(encoded) < len(trimmed)):
                return AudioPayload(encoded, "audio/ogg", original_seconds, seconds,
                                    time.perf_counter() - started)
        except (OSError, subprocess.SubprocessError):
            pass  # Keep the request working if the optional encoder is unavailable or slow.
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(trimmed)
    return AudioPayload(out.getvalue(), "audio/wav", original_seconds, seconds,
                        time.perf_counter() - started)
