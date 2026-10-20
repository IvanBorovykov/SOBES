import io
import math
import struct
import subprocess
import unittest
import wave
from unittest.mock import Mock, patch

from audio_payload import _ffmpeg, prepare_audio, trim_silence


def tone(seconds, amplitude=2000):
    return b"".join(struct.pack('<h', int(amplitude * math.sin(i * 2 * math.pi * 220 / 16000)))
                    for i in range(int(seconds * 16000)))


class AudioPayloadTests(unittest.TestCase):
    def test_trims_only_edges_with_padding_and_keeps_internal_silence(self):
        speech = tone(.5)
        middle = bytes(32000)
        pcm = bytes(32000) + speech + middle + speech + bytes(32000)
        trimmed = trim_silence(pcm)
        self.assertEqual(trimmed, bytes(9600) + speech + middle + speech + bytes(9600))

    def test_quiet_speech_is_preserved(self):
        pcm = tone(1, amplitude=20) + tone(1)
        self.assertEqual(trim_silence(pcm), pcm)
        self.assertEqual(trim_silence(tone(1) + tone(1, amplitude=20)), tone(1) + tone(1, amplitude=20))
        almost_silent = struct.pack('<h', 2) * 16000
        self.assertEqual(trim_silence(almost_silent), almost_silent)

    def test_empty_and_digital_silence_do_not_create_a_fake_voice_question(self):
        self.assertIsNone(prepare_audio(b""))
        self.assertIsNone(prepare_audio(bytes(32000)))

    def test_wav_fallback_preserves_trimmed_pcm_when_encoder_is_missing(self):
        pcm = bytes(32000) + tone(1) + bytes(32000)
        with patch('audio_payload._ffmpeg', return_value=None):
            payload = prepare_audio(pcm)
        self.assertEqual(payload.mime_type, 'audio/wav')
        with wave.open(io.BytesIO(payload.data)) as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.readframes(wav.getnframes()), trim_silence(pcm))
        self.assertAlmostEqual(payload.original_seconds, 3)
        self.assertAlmostEqual(payload.sent_seconds, 1.6)

    def test_encoder_timeout_falls_back_to_valid_wav(self):
        with patch('audio_payload._ffmpeg', return_value='ffmpeg'), \
                patch('audio_payload.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', .5)):
            payload = prepare_audio(tone(2))
        self.assertEqual(payload.mime_type, 'audio/wav')
        with wave.open(io.BytesIO(payload.data)) as wav:
            self.assertEqual(wav.getnframes(), 32000)

    def test_unexpected_encoder_output_is_not_mislabeled_as_ogg(self):
        with patch('audio_payload._ffmpeg', return_value='ffmpeg'), \
                patch('audio_payload.subprocess.run', return_value=Mock(stdout=b'not opus')):
            payload = prepare_audio(tone(2))
        self.assertEqual(payload.mime_type, 'audio/wav')

    @unittest.skipUnless(_ffmpeg(), 'Optional ffmpeg is not installed')
    def test_real_opus_roundtrip_is_smaller_and_retains_duration(self):
        pcm = tone(2)
        payload = prepare_audio(pcm)
        self.assertEqual(payload.mime_type, 'audio/ogg')
        self.assertLess(len(payload.data), len(pcm) // 3)
        decoded = subprocess.run([_ffmpeg(), '-hide_banner', '-loglevel', 'error',
                                  '-i', 'pipe:0', '-f', 's16le', '-ar', '16000', '-ac', '1', 'pipe:1'],
                                 input=payload.data, capture_output=True, check=True, timeout=2).stdout
        self.assertLessEqual(abs(len(decoded) - len(pcm)), 640)


if __name__ == '__main__':
    unittest.main()
