import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preferences import Preferences


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.json"
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AUDIO_BUFFER_SECONDS", None)

    def test_default_and_saved_preferences_round_trip(self):
        preferences = Preferences(self.path)
        self.assertEqual(preferences.audio_seconds, 30)
        preferences.context_files = ["/tmp/guide.md"]
        preferences.audio_seconds = 45
        preferences.capture_display = "monitor-a"
        preferences.overlay_display = "monitor-b"
        preferences.controls_display = "monitor-a"
        preferences.overlay_origin = [-100, 1200]
        preferences.audio_source = "system"
        preferences.save()
        restored = Preferences(self.path)
        self.assertEqual(restored.audio_source, "system")
        self.assertEqual(restored.context_files, ["/tmp/guide.md"])
        self.assertEqual(restored.audio_seconds, 45)
        self.assertEqual(restored.capture_display, "monitor-a")
        self.assertEqual(restored.overlay_display, "monitor-b")
        self.assertEqual(restored.overlay_origin, [-100, 1200])

    def test_invalid_values_do_not_reach_native_window_geometry(self):
        self.path.write_text(json.dumps({"audio_seconds": True, "overlay_origin": [float("nan"), 3],
                                         "controls_origin": ["bad", 3], "capture_display": []}))
        preferences = Preferences(self.path)
        self.assertEqual(preferences.audio_seconds, 30)
        self.assertIsNone(preferences.overlay_origin)
        self.assertIsNone(preferences.controls_origin)
        self.assertIsNone(preferences.capture_display)

    def test_explicit_environment_overrides_saved_audio_length(self):
        self.path.write_text('{"audio_seconds": 45}')
        os.environ["AUDIO_BUFFER_SECONDS"] = "15"
        self.assertEqual(Preferences(self.path).audio_seconds, 15)


if __name__ == "__main__":
    unittest.main()
