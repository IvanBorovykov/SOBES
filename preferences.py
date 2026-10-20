"""Local display and audio preferences. No credentials are stored here."""

import json
import math
import os
from pathlib import Path


DEFAULT_PATH = Path.home() / "Library/Application Support/InterviewAssistant/settings.json"
MAX_AUDIO_SECONDS = 60


class Preferences:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.audio_seconds = 30
        self.capture_display = None
        self.overlay_display = None
        self.controls_display = None
        self.overlay_origin = None
        self.controls_origin = None
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as error:
            print(f"Не удалось прочитать настройки: {error}")
            data = {}
        if not isinstance(data, dict):
            data = {}
        seconds = data.get("audio_seconds", 30)
        if type(seconds) is int and 1 <= seconds <= MAX_AUDIO_SECONDS:
            self.audio_seconds = seconds
        for name in ("capture_display", "overlay_display", "controls_display"):
            value = data.get(name)
            if isinstance(value, str):
                setattr(self, name, value)
        for name in ("overlay_origin", "controls_origin"):
            value = data.get(name)
            if (isinstance(value, list) and len(value) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) for v in value)):
                setattr(self, name, value)

        # Explicit shell configuration overrides saved preferences for this launch.
        if "AUDIO_BUFFER_SECONDS" in os.environ:
            self.audio_seconds = int(os.environ["AUDIO_BUFFER_SECONDS"])
        if not 1 <= self.audio_seconds <= MAX_AUDIO_SECONDS:
            raise ValueError("AUDIO_BUFFER_SECONDS must be between 1 and 60")

    def save(self):
        data = {name: getattr(self, name) for name in (
            "audio_seconds", "capture_display", "overlay_display", "controls_display",
            "overlay_origin", "controls_origin",
        )}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.path)
