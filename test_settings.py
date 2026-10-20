import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from AppKit import NSMakeRect
from PIL import Image

import assistant
from preferences import Preferences


class CaptureSelectionTests(unittest.TestCase):
    def test_fallback_keeps_the_selected_monitor_region(self):
        bounds = NSMakeRect(-1920, -1080, 1920, 1080)

        def capture(args, **kwargs):
            self.assertEqual(args[:3], ["screencapture", "-x", "-R-1920,-1080,1920,1080"])
            Image.new("RGB", (32, 24)).save(args[-1])

        with patch.object(assistant.Quartz, "CGDisplayIsActive", return_value=True), \
                patch.object(assistant.Quartz, "CGDisplayBounds", return_value=bounds), \
                patch.object(assistant.mss, "mss", side_effect=OSError("Use fallback")), \
                patch.object(assistant.subprocess, "run", side_effect=capture):
            result = assistant.capture_screenshot(3)
        self.assertTrue(result.startswith(b"\xff\xd8"))

    def test_fast_capture_returns_a_small_jpeg(self):
        bounds = NSMakeRect(0, 0, 3840, 2160)

        def capture(args, **kwargs):
            Image.effect_noise((3840, 2160), 80).convert("RGB").save(args[-1])

        with patch.object(assistant.Quartz, "CGDisplayIsActive", return_value=True), \
                patch.object(assistant.Quartz, "CGDisplayBounds", return_value=bounds), \
                patch.object(assistant.mss, "mss", side_effect=OSError("Use fallback")), \
                patch.object(assistant.subprocess, "run", side_effect=capture):
            result = assistant.capture_screenshot(3, fast=True)
        self.assertTrue(result.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(result), 100_000)

    def test_disconnected_monitor_does_not_capture_another_screen(self):
        with patch.object(assistant.mss, "mss") as capture:
            with self.assertRaisesRegex(ValueError, "отключён"):
                assistant.capture_screenshot(-1)
            capture.assert_not_called()


class Panel:
    def __init__(self, width, height):
        self.rectangle = NSMakeRect(0, 0, width, height)

    def frame(self):
        return self.rectangle

    def setContentSize_(self, size):
        self.rectangle.size.width = size.width
        self.rectangle.size.height = size.height + 32

    def setFrameOrigin_(self, point):
        self.rectangle.origin = point


class DisplaySettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        preferences = Preferences(Path(directory.name) / "settings.json")
        preferences.overlay_display = "external"
        preferences.controls_display = "primary"
        preferences.capture_display = "external"
        preferences.audio_seconds = 45
        self.controls = SimpleNamespace(
            _preferences=preferences, _overlay=SimpleNamespace(_panel=Panel(560, 792)),
            _panel=Panel(620, 210), _assistant=SimpleNamespace(capture_display_id=None),
            _audio=Mock(source="microphone", error=""), update_status=Mock(),
        )
        self.displays = [
            {"key": "primary", "id": 1, "screen": SimpleNamespace(visibleFrame=lambda: NSMakeRect(0, 0, 1728, 1084))},
            {"key": "external", "id": 3, "screen": SimpleNamespace(visibleFrame=lambda: NSMakeRect(-103, 1117, 1920, 720))},
        ]

    def test_output_and_capture_screens_are_independent_and_fit(self):
        with patch.object(assistant, "get_displays", return_value=self.displays):
            assistant.Controls.restore_layout(self.controls)
        self.assertEqual(self.controls._assistant.capture_display_id, 3)
        self.controls._audio.set_seconds.assert_called_once_with(45)
        overlay = self.controls._overlay._panel.frame()
        toolbar = self.controls._panel.frame()
        self.assertGreaterEqual(overlay.origin.y, 1117)
        self.assertLessEqual(overlay.origin.y + overlay.size.height, 1117 + 720)
        self.assertGreaterEqual(toolbar.origin.y, 0)
        self.assertLess(toolbar.origin.y, 1084)

    def test_missing_capture_screen_is_blocked_while_windows_remain_reachable(self):
        with patch.object(assistant, "get_displays", return_value=self.displays[:1]):
            assistant.Controls.restore_layout(self.controls)
        self.assertEqual(self.controls._assistant.capture_display_id, -1)
        self.assertEqual(self.controls._preferences.capture_display, "external")
        self.assertEqual(self.controls._preferences.overlay_display, "primary")

    def test_changing_output_screen_clears_only_its_saved_position(self):
        self.controls._preferences.overlay_origin = [-100, 1200]
        self.controls._preferences.controls_origin = [100, 700]
        self.controls.restore_layout = Mock()
        with patch.object(assistant, "get_displays", return_value=self.displays):
            assistant.Controls.apply_preferences(self.controls, {
                "overlay_display": "primary", "controls_display": "primary",
                "capture_display": "external", "audio_seconds": 30,
            })
        self.assertIsNone(self.controls._preferences.overlay_origin)
        self.assertEqual(self.controls._preferences.controls_origin, [100, 700])


if __name__ == "__main__":
    unittest.main()
