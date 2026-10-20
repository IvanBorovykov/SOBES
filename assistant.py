#!/usr/bin/env python3
"""Interview assistant: rolling mic buffer + screenshot -> Gemini (free tier) -> overlay hidden from screen sharing.

Hotkeys (swallowed, so the focused app never sees them):
    Option+X   detailed text/code answer from recent audio + screen
    Option+Z   short Live hint from recent audio + screen
    Option+Q   short Live hint from recent audio only
Other actions are available on the floating toolbar.
"""

import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import mss
import objc
import pyaudio
import Quartz
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationDidChangeScreenParametersNotification,
    NSAlert,
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSButton,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSFloatingWindowLevel,
    NSEventTypeLeftMouseDown,
    NSEventTypeLeftMouseDragged,
    NSEventTypeLeftMouseUp,
    NSPanel,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSScreen,
    NSTextView,
    NSTextField,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowSharingNone,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSMakePoint, NSMakeRect, NSMakeRange, NSMakeSize, NSNotificationCenter
from google.genai import errors, types
from PIL import Image
from PyObjCTools import AppHelper

from live_connection import LiveConnection
from realtime_hints import RealtimeHints
from api_keys import KeychainError, KeychainStore, KeyPool, credential_error
from api_keys_ui import ApiKeysPanel
from preferences import MAX_AUDIO_SECONDS, Preferences

# --- Config -----------------------------------------------------------------

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
CHUNK_FRAMES = 320
BUFFER_SECONDS = int(os.environ.get("AUDIO_BUFFER_SECONDS", "30"))
if not 1 <= BUFFER_SECONDS <= MAX_AUDIO_SECONDS:
    raise ValueError("AUDIO_BUFFER_SECONDS must be between 1 and 60")

MAX_SCREENSHOT_WIDTH = 1280
JPEG_QUALITY = 70

PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash")
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE")  # substring of an input device name, e.g. "BlackHole"
MONITOR_INDEX = int(os.environ.get("MONITOR", "1"))  # mss index: 1 = primary display

SYSTEM_INSTRUCTION = (
    "Ти — помічник на IT-співбесіді. Проаналізуй питання в доданому аудіо "
    "(якщо додано) та задачу на скріншоті (якщо додано). Відповідай українською. Почни одразу "
    "з прямої відповіді у 1–2 реченнях, без вступу та повторення питання. "
    "Потім додай короткі деталі. Готовий код додавай лише для задачі з кодом."
)
USER_PROMPT = "Аудіо — останній фрагмент розмови, зображення — поточний екран."
LIVE_INSTRUCTION = (
    "Ти — помічник на IT-співбесіді. Після завершення кожної активності дай коротку "
    "відповідь українською у 1–3 реченнях на останнє питання з поточного аудіо та "
    "поточного знімка екрана, якщо його додано. Якщо зображення немає, відповідай лише за аудіо. Почни одразу з відповіді, без вступу. Не повторюй "
    "попередні відповіді. Для задачі з кодом коротко поясни підхід, не диктуй код."
)

OVERLAY_WIDTH = 560
OVERLAY_HEIGHT_RATIO = 0.7
SCROLL_STEP = 120

KEY_X, KEY_Z, KEY_Q = 7, 6, 12
MODIFIER_MASK = (
    Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskShift
    | Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskAlternate
)
HOTKEY_MODIFIERS = Quartz.kCGEventFlagMaskAlternate


# --- Audio ring buffer --------------------------------------------------------

class AudioRing:
    """Keep up to 60 seconds locally; send only the selected recent audio window."""

    def __init__(self):
        self._chunks = deque(maxlen=-(-MAX_AUDIO_SECONDS * SAMPLE_RATE // CHUNK_FRAMES))
        self._seconds = BUFFER_SECONDS
        self._lock = threading.Lock()
        self._listeners = []
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=self._find_device(),
            frames_per_buffer=CHUNK_FRAMES,
            stream_callback=self._on_audio,
        )

    def _find_device(self):
        if not AUDIO_DEVICE:
            self.device_name = self._pa.get_default_input_device_info()["name"]
            return None
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0 and AUDIO_DEVICE.lower() in info["name"].lower():
                self.device_name = info["name"]
                print(f"Audio input: {info['name']}")
                return i
        sys.exit(f"No audio input device matching {AUDIO_DEVICE!r}")

    def _on_audio(self, data, frame_count, time_info, status):
        with self._lock:
            self._chunks.append(data)
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(data)
        return None, pyaudio.paContinue

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener):
        with self._lock:
            self._listeners.remove(listener)

    def pcm_bytes(self):
        with self._lock:
            return b"".join(self._chunks)[-self._seconds * SAMPLE_RATE * SAMPLE_WIDTH:]

    def set_seconds(self, seconds):
        if type(seconds) is not int or not 1 <= seconds <= MAX_AUDIO_SECONDS:
            raise ValueError("Длительность аудио должна быть от 1 до 60 секунд")
        with self._lock:
            self._seconds = seconds

    def wav_bytes(self, pcm=None):
        if pcm is None:
            pcm = self.pcm_bytes()
        if not pcm:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(SAMPLE_WIDTH)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def clear(self):
        with self._lock:
            self._chunks.clear()


# --- Screenshot ---------------------------------------------------------------

def get_displays():
    displays = []
    for index, screen in enumerate(NSScreen.screens()):
        display_id = int(screen.deviceDescription()["NSScreenNumber"])
        serial = Quartz.CGDisplaySerialNumber(display_id)
        key = f"{Quartz.CGDisplayVendorNumber(display_id)}:{Quartz.CGDisplayModelNumber(display_id)}:{serial}"
        if not serial:
            key += f":{display_id}"
        name = str(screen.localizedName())
        width, height = Quartz.CGDisplayPixelsWide(display_id), Quartz.CGDisplayPixelsHigh(display_id)
        displays.append({
            "id": display_id, "key": key, "name": name, "screen": screen,
            "title": f"{index + 1}. {name} — {width} × {height}",
        })
    return displays


def capture_screenshot(display_id=None, fast=False):
    if display_id is None:
        display_id = Quartz.CGMainDisplayID()
    if display_id < 0 or not Quartz.CGDisplayIsActive(display_id):
        raise ValueError("Экран для анализа отключён. Выберите экран в настройках.")
    bounds = Quartz.CGDisplayBounds(display_id)
    region = {"left": int(bounds.origin.x), "top": int(bounds.origin.y),
              "width": int(bounds.size.width), "height": int(bounds.size.height)}
    try:
        with mss.mss() as sct:
            shot = sct.grab(region)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        # mss relies on CGWindowListCreateImage, which newer macOS versions may refuse.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "screen.png")
            rectangle = ",".join(str(region[key]) for key in ("left", "top", "width", "height"))
            subprocess.run(["screencapture", "-x", f"-R{rectangle}", path], check=True,
                           timeout=3 if fast else 15)
            img = Image.open(path).convert("RGB")

    if fast:
        factor = max(1, img.width // MAX_SCREENSHOT_WIDTH)
        if factor > 1:
            img = img.reduce(factor)
        img.thumbnail((MAX_SCREENSHOT_WIDTH, MAX_SCREENSHOT_WIDTH), Image.Resampling.BILINEAR)
    elif img.width > MAX_SCREENSHOT_WIDTH:
        img = img.resize(
            (MAX_SCREENSHOT_WIDTH, round(img.height * MAX_SCREENSHOT_WIDTH / img.width)),
            Image.Resampling.LANCZOS,
        )
    while True:
        out = io.BytesIO()
        img.save(out, "JPEG", quality=60 if fast else JPEG_QUALITY, optimize=not fast)
        result = out.getvalue()
        if not fast or len(result) <= 100_000 or img.width <= 320:
            return result
        img = img.resize((max(1, img.width * 4 // 5), max(1, img.height * 4 // 5)),
                         Image.Resampling.BILINEAR)


# --- Overlay ------------------------------------------------------------------

class DraggablePanel(NSPanel):
    """Track title-bar dragging directly, including on accessory app panels."""

    def sendEvent_(self, event):
        kind = event.type()
        if kind == NSEventTypeLeftMouseDown and event.locationInWindow().y >= self.contentView().bounds().size.height:
            self._drag_offset = event.locationInWindow()
            return
        offset = getattr(self, "_drag_offset", None)
        if offset is not None and kind == NSEventTypeLeftMouseDragged:
            point = self.convertPointToScreen_(event.locationInWindow())
            self.setFrameOrigin_(NSMakePoint(point.x - offset.x, point.y - offset.y))
            return
        if offset is not None and kind == NSEventTypeLeftMouseUp:
            self._drag_offset = None
            return
        objc.super(DraggablePanel, self).sendEvent_(event)


class Overlay:
    """Floating answer panel with the standard macOS draggable title bar."""

    def __init__(self):
        visible = NSScreen.mainScreen().visibleFrame()
        height = max(200, min(visible.size.height * OVERLAY_HEIGHT_RATIO, visible.size.height - 360))
        frame = NSMakeRect(
            visible.origin.x + visible.size.width - OVERLAY_WIDTH - 20,
            visible.origin.y + (visible.size.height - height) / 2,
            OVERLAY_WIDTH,
            height,
        )

        panel = DraggablePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskTitled,
            NSBackingStoreBuffered,
            False,
        )
        panel.setSharingType_(NSWindowSharingNone)
        panel.setTitle_("Подсказка — перетащите окно за этот заголовок")
        panel.setMovable_(True)
        panel.setMovableByWindowBackground_(True)
        panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        panel.setIgnoresMouseEvents_(False)
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        panel.setHidesOnDeactivate_(False)
        panel.setOpaque_(False)
        panel.setHasShadow_(False)
        panel.setBackgroundColor_(NSColor.clearColor())

        content = panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.06, 0.82).CGColor())
        content.layer().setCornerRadius_(12)

        scroll = NSTextView.scrollableTextView()
        scroll.setFrame_(NSMakeRect(14, 12, OVERLAY_WIDTH - 28, height - 24))
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(False)
        text = scroll.documentView()
        text.setEditable_(False)
        text.setSelectable_(False)
        text.setDrawsBackground_(False)
        content.addSubview_(scroll)

        self._panel = panel
        self._scroll = scroll
        self._text = text
        self._attrs = {
            NSFontAttributeName: NSFont.fontWithName_size_("Menlo", 13),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(0.95, 1.0),
        }
        self._follow = False

    def show_text(self, text, reset_scroll=False):
        if reset_scroll:
            self._follow = False
        storage = self._text.textStorage()
        storage.setAttributedString_(NSAttributedString.alloc().initWithString_attributes_(text, self._attrs))
        if reset_scroll:
            self._text.scrollRangeToVisible_(NSMakeRange(0, 0))
        elif self._follow:
            self._text.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))
        self._panel.orderFrontRegardless()

    def hide(self):
        self._panel.orderOut_(None)
        self._text.setString_("")

    def scroll(self, delta):
        clip = self._scroll.contentView()
        max_y = max(0, self._text.frame().size.height - clip.bounds().size.height)
        y = min(max(clip.bounds().origin.y + delta, 0), max_y)
        self._follow = delta > 0 and y >= max_y
        clip.scrollToPoint_(NSMakePoint(0, y))
        self._scroll.reflectScrolledClipView_(clip)


# --- Controls -----------------------------------------------------------------

class SettingsPanel(NSObject):
    def initWithApply_move_(self, apply, move):
        self = objc.super(SettingsPanel, self).init()
        if self is None:
            return None
        self._apply = apply
        self._move = move
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 640, 480), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False,
        )
        self._panel.setTitle_("Настройки помощника")
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setLevel_(NSFloatingWindowLevel + 1)
        self._panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self._panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | NSWindowCollectionBehaviorFullScreenAuxiliary)
        content = self._panel.contentView()
        self._popups = {}
        for name, title, y in (
            ("overlay_display", "Где показывать подсказку", 440),
            ("controls_display", "Где показывать панель с кнопками", 366),
            ("capture_display", "Какой экран отправлять модели", 292),
            ("audio_seconds", "Сколько последних секунд аудио отправлять", 218),
        ):
            label = NSTextField.labelWithString_(title)
            label.setFrame_(NSMakeRect(20, y, 600, 20))
            content.addSubview_(label)
            popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(18, y - 33, 604, 28), False)
            popup.setAccessibilityLabel_(title)
            content.addSubview_(popup)
            self._popups[name] = popup
        label = NSTextField.labelWithString_("Переместить текущее окно подсказки")
        label.setFrame_(NSMakeRect(20, 150, 600, 20))
        content.addSubview_(label)
        for title, action, x, width in (
            ("↑ Выше", "moveUp:", 18, 130), ("↓ Ниже", "moveDown:", 154, 130),
            ("Сбросить положение", "resetPosition:", 290, 218),
        ):
            button = NSButton.buttonWithTitle_target_action_(title, self, action)
            button.setFrame_(NSMakeRect(x, 112, width, 30))
            content.addSubview_(button)
        self._info = NSTextField.wrappingLabelWithString_("")
        self._info.setFont_(NSFont.systemFontOfSize_(11))
        self._info.setTextColor_(NSColor.secondaryLabelColor())
        self._info.setFrame_(NSMakeRect(20, 54, 600, 48))
        content.addSubview_(self._info)
        for title, action, x in (("Отмена", "cancel:", 392), ("Применить", "apply:", 506)):
            button = NSButton.buttonWithTitle_target_action_(title, self, action)
            button.setFrame_(NSMakeRect(x, 14, 114, 30))
            content.addSubview_(button)
        quit_button = NSButton.buttonWithTitle_target_action_("Завершить", self, "quit:")
        quit_button.setFrame_(NSMakeRect(18, 14, 130, 30))
        content.addSubview_(quit_button)
        self._keys_button = NSButton.buttonWithTitle_target_action_("API-ключи…", None, None)
        self._keys_button.setFrame_(NSMakeRect(158, 14, 142, 30))
        content.addSubview_(self._keys_button)
        return self

    @objc.python_method
    def show(self, preferences, audio_name):
        displays = get_displays()
        for name in ("overlay_display", "controls_display", "capture_display"):
            popup = self._popups[name]
            popup.removeAllItems()
            selected = getattr(preferences, name)
            keys = [display["key"] for display in displays]
            if selected not in keys:
                popup.addItemWithTitle_("Экран отключён — выберите другой")
            for display in displays:
                popup.addItemWithTitle_(display["title"])
                popup.lastItem().setRepresentedObject_(display["key"])
                if display["key"] == selected:
                    popup.selectItem_(popup.lastItem())
        popup = self._popups["audio_seconds"]
        popup.removeAllItems()
        for seconds in sorted({8, 15, 30, 45, 60, preferences.audio_seconds}):
            popup.addItemWithTitle_(f"{seconds} секунд" + (" — больше контекста" if seconds == 30 else ""))
            popup.lastItem().setTag_(seconds)
            if seconds == preferences.audio_seconds:
                popup.selectItem_(popup.lastItem())
        self._info.setStringValue_(
            f"Источник звука: {audio_name}.\n"
            "8 с — меньше данных, но вопрос может обрезаться. 30 с — больше контекста. "
            "Длительность влияет на объём запроса, а не задаёт время ожидания."
        )
        self._panel.center()
        self._panel.makeKeyAndOrderFront_(None)

    def apply_(self, sender):
        values = {name: popup.selectedItem().representedObject() for name, popup in self._popups.items()
                  if name != "audio_seconds"}
        values["audio_seconds"] = self._popups["audio_seconds"].selectedItem().tag()
        try:
            self._apply(values)
        except (ValueError, OSError) as error:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Не удалось применить настройки")
            alert.setInformativeText_(str(error))
            alert.runModal()
            return
        self._panel.orderOut_(None)

    def cancel_(self, sender):
        self._panel.orderOut_(None)

    def quit_(self, sender):
        AppHelper.stopEventLoop()

    def moveUp_(self, sender):
        self._move(40)

    def moveDown_(self, sender):
        self._move(-40)

    def resetPosition_(self, sender):
        self._move(None)


class WindowObserver(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(WindowObserver, self).init()
        if self is not None:
            self._callback = callback
        return self

    def windowDidMove_(self, notification):
        self._callback(notification.object())


class Controls(NSObject):
    """Native draggable toolbar, display preferences and shortcut labels."""

    def initWithActions_(self, actions):
        self = objc.super(Controls, self).init()
        if self is None:
            return None
        self._actions = actions
        panel = DraggablePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(50, 50, 620, 254), NSWindowStyleMaskTitled,
            NSBackingStoreBuffered, False,
        )
        panel.setTitle_("Помощник — перетащите окно за этот заголовок")
        panel.setSharingType_(NSWindowSharingNone)
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                    | NSWindowCollectionBehaviorFullScreenAuxiliary
                                    | NSWindowCollectionBehaviorIgnoresCycle)
        panel.setHidesOnDeactivate_(False)
        panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        panel.setMovable_(True)
        panel.setMovableByWindowBackground_(True)
        content = panel.contentView()
        self._status = NSTextField.wrappingLabelWithString_("")
        self._status.setFont_(NSFont.systemFontOfSize_(11))
        self._status.setTextColor_(NSColor.secondaryLabelColor())
        self._status.setFrame_(NSMakeRect(16, 8, 588, 112))
        content.addSubview_(self._status)
        self._buttons = []
        specs = (
            ("Только звук  ⌥Q", "audio", 14, 208, 190),
            ("Подсказка  ⌥Z", "live", 212, 208, 208),
            ("Ответ / код  ⌥X", "answer", 428, 208, 178),
            ("Только экран", "screen", 14, 168, 190),
            ("Очистить", "clear", 212, 168, 208),
            ("Настройки…", "settings", 428, 168, 178),
            ("Поток: пауза / пуск", "streaming", 14, 128, 406),
            ("↑", "up", 428, 128, 85),
            ("↓", "down", 521, 128, 85),
        )
        self._names = [spec[1] for spec in specs]
        for index, (title, name, x, y, width) in enumerate(specs):
            button = NSButton.buttonWithTitle_target_action_(title, self, "press:")
            button.setFrame_(NSMakeRect(x, y, width, 32))
            button.setFont_(NSFont.systemFontOfSize_(12))
            button.setTag_(index)
            content.addSubview_(button)
            self._buttons.append(button)
        self._panel = panel
        self._placing = False
        return self

    @objc.python_method
    def configure(self, preferences, overlay, audio, assistant):
        self._preferences, self._overlay, self._audio, self._assistant = preferences, overlay, audio, assistant
        self._settings = SettingsPanel.alloc().initWithApply_move_(self.apply_preferences, self.move_overlay)
        self._keys_panel = ApiKeysPanel.alloc().initWithPool_saved_(assistant.keys, assistant.credentials_changed)
        self._settings._keys_button.setTarget_(self)
        self._settings._keys_button.setAction_("openKeys:")
        assistant.keys.on_change = lambda: AppHelper.callAfter(self.update_status)
        self._actions["settings"] = lambda: self._settings.show(self._preferences, self._audio.device_name)
        menu = NSMenu.alloc().initWithTitle_("Помощник")
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Помощник", None, "")
        submenu = NSMenu.alloc().initWithTitle_("Помощник")
        for title, action, key in (("Настройки…", "openSettings:", ","), ("Завершить помощника", "quit:", "q")):
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            entry.setTarget_(self)
            submenu.addItem_(entry)
        item.setSubmenu_(submenu)
        menu.addItem_(item)
        edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Правка", None, "")
        edit_menu = NSMenu.alloc().initWithTitle_("Правка")
        for title, action, key in (("Отменить", "undo:", "z"), ("Вырезать", "cut:", "x"),
                                   ("Копировать", "copy:", "c"), ("Вставить", "paste:", "v"),
                                   ("Выделить всё", "selectAll:", "a")):
            edit_menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key))
        edit_item.setSubmenu_(edit_menu)
        menu.addItem_(edit_item)
        NSApplication.sharedApplication().setMainMenu_(menu)
        self._observers = []
        for kind, panel in (("overlay", overlay._panel), ("controls", self._panel)):
            observer = WindowObserver.alloc().initWithCallback_(lambda window, kind=kind: self.remember_window(kind, window))
            panel.setDelegate_(observer)
            self._observers.append(observer)
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "screensChanged:", NSApplicationDidChangeScreenParametersNotification, None,
        )
        displays = get_displays()
        if preferences.capture_display is None or "MONITOR" in os.environ:
            preferences.capture_display = (displays[MONITOR_INDEX - 1]["key"]
                                           if 1 <= MONITOR_INDEX <= len(displays)
                                           else f"missing-monitor:{MONITOR_INDEX}")
        self.restore_layout()

    @objc.python_method
    def restore_layout(self):
        displays = get_displays()
        if not displays:
            return
        by_key = {display["key"]: display for display in displays}
        self._placing = True
        try:
            for kind, panel in (("overlay", self._overlay._panel), ("controls", self._panel)):
                display = by_key.get(getattr(self._preferences, f"{kind}_display"), displays[0])
                setattr(self._preferences, f"{kind}_display", display["key"])
                visible = display["screen"].visibleFrame()
                if kind == "overlay":
                    height = max(200, min(visible.size.height * OVERLAY_HEIGHT_RATIO, visible.size.height - 360))
                    panel.setContentSize_(NSMakeSize(OVERLAY_WIDTH, height))
                size = panel.frame().size
                origin = getattr(self._preferences, f"{kind}_origin")
                x, y = origin if origin else (
                    visible.origin.x + visible.size.width - size.width - 20,
                    visible.origin.y + (20 if kind == "overlay" else visible.size.height - size.height - 16),
                )
                x = max(visible.origin.x, min(x, visible.origin.x + max(0, visible.size.width - size.width)))
                y = max(visible.origin.y, min(y, visible.origin.y + max(0, visible.size.height - size.height)))
                panel.setFrameOrigin_(NSMakePoint(x, y))
                setattr(self._preferences, f"{kind}_origin", [x, y])
        finally:
            self._placing = False
        capture = by_key.get(self._preferences.capture_display)
        self._assistant.capture_display_id = capture["id"] if capture else -1
        self._audio.set_seconds(self._preferences.audio_seconds)
        realtime = getattr(self._assistant, "_realtime", None)
        if realtime:
            realtime.set_display(self._assistant.capture_display_id)
        self.update_status()

    @objc.python_method
    def update_status(self):
        displays = {display["key"]: display for display in get_displays()}
        lines = []
        for name, title in (("capture_display", "Экран для снимков"), ("overlay_display", "Подсказка"),
                            ("controls_display", "Панель кнопок")):
            display = displays.get(getattr(self._preferences, name))
            lines.append(f"{title}: {display['title'] if display else 'экран отключён — откройте настройки'}")
        lines.append(f"Аудио: {self._audio.device_name} · буфер для ответа / резерва {self._preferences.audio_seconds} с")
        lines.append(self._assistant.keys.status())
        realtime = getattr(self._assistant, "_realtime", None)
        lines.append(realtime.status() if realtime else "Поток: запуск…")
        self._buttons[self._names.index("streaming")].setTitle_(
            "Пауза потока" if realtime and realtime.enabled else "Включить поток")
        self._status.setStringValue_("\n".join(lines))
        self._status.setToolTip_("\n".join(lines))

    @objc.python_method
    def remember_window(self, kind, panel):
        if self._placing:
            return
        screen = panel.screen()
        if screen is None:
            return
        display = next((d for d in get_displays() if d["id"] == int(screen.deviceDescription()["NSScreenNumber"])), None)
        if display:
            setattr(self._preferences, f"{kind}_display", display["key"])
            origin = panel.frame().origin
            setattr(self._preferences, f"{kind}_origin", [origin.x, origin.y])
            self.update_status()
            self.save_(None)

    def save_(self, sender):
        try:
            self._preferences.save()
        except OSError as error:
            print(f"Не удалось сохранить положение окон: {error}")

    @objc.python_method
    def move_overlay(self, distance):
        panel = self._overlay._panel
        if distance is None:
            self._preferences.overlay_origin = None
            self.restore_layout()
        else:
            visible = panel.screen().visibleFrame()
            frame = panel.frame()
            y = max(visible.origin.y, min(frame.origin.y + distance,
                    visible.origin.y + max(0, visible.size.height - frame.size.height)))
            panel.setFrameOrigin_(NSMakePoint(frame.origin.x, y))
            self.remember_window("overlay", panel)
        self._preferences.save()
        panel.orderFrontRegardless()

    @objc.python_method
    def apply_preferences(self, values):
        keys = {display["key"] for display in get_displays()}
        if any(values[name] not in keys for name in ("overlay_display", "controls_display", "capture_display")):
            raise ValueError("Выберите подключённый экран в каждом списке.")
        for kind in ("overlay", "controls"):
            if values[f"{kind}_display"] != getattr(self._preferences, f"{kind}_display"):
                setattr(self._preferences, f"{kind}_origin", None)
        for name, value in values.items():
            setattr(self._preferences, name, value)
        self.restore_layout()
        self._preferences.save()

    def screensChanged_(self, notification):
        self.restore_layout()

    def openSettings_(self, sender):
        self._actions["settings"]()

    def openKeys_(self, sender):
        self._keys_panel.show()

    def quit_(self, sender):
        AppHelper.stopEventLoop()

    def press_(self, sender):
        self._actions[self._names[sender.tag()]]()

    @objc.python_method
    def show(self):
        self._panel.orderFrontRegardless()

# --- Gemini -------------------------------------------------------------------

class Assistant:
    def __init__(self, overlay, audio, keys):
        self._overlay = overlay
        self._audio = audio
        self.keys = keys
        self._capture_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="capture")
        candidates = keys.candidates(LIVE_MODEL)
        client = keys.client(candidates[0]) if candidates else None
        self._live = LiveConnection(client, LIVE_MODEL, LIVE_INSTRUCTION,
                                    redact=keys.redact, on_key_error=keys.failed_client)
        self.capture_display_id = Quartz.CGMainDisplayID()
        self._realtime = None
        self._generation = 0  # bumped on every trigger/clear; stale streams stop rendering

    def start_realtime(self, on_status=None):
        self._live.reset(None)  # Keep the on-demand fallback disconnected until needed.
        self._realtime = RealtimeHints(
            self.keys, LIVE_MODEL, LIVE_INSTRUCTION,
            lambda display_id: capture_screenshot(display_id, fast=True),
            self.capture_display_id, on_status,
        )
        self._audio.subscribe(self._realtime.offer_audio)

    def toggle_realtime(self):
        if self._realtime:
            self._generation += 1
            self._realtime.set_enabled(not self._realtime.enabled)

    def analyze(self, screen_only=False, live=False, audio_only=False):
        self._generation += 1
        started = time.perf_counter()
        preparing = "Відкриваю потокову підказку" if live and self._realtime and self._realtime.enabled else (
            "Готую аудіо без екрана" if audio_only else "Готую знімок екрана")
        self._overlay.show_text(f"⏳ {preparing}…  ·  «Очистить» — скасувати", reset_scroll=True)
        threading.Thread(
            target=self._run_live if live else self._run,
            args=(self._generation, started, screen_only), daemon=True,
            kwargs={"capture_display_id": self.capture_display_id, "audio_only": audio_only},
        ).start()

    def clear(self):
        self._generation += 1
        self._audio.clear()
        self._overlay.hide()
        if self._realtime:
            self._realtime.reset()
        candidates = self.keys.candidates(LIVE_MODEL)
        self._live.reset(None if self._realtime else self.keys.client(candidates[0]) if candidates else None)

    def close(self):
        self._generation += 1
        if self._realtime:
            self._audio.unsubscribe(self._realtime.offer_audio)
            self._realtime.close()
        self._live.close()
        self._capture_pool.shutdown(wait=False, cancel_futures=True)
        self.keys.close()

    def credentials_changed(self):
        self._generation += 1
        candidates = self.keys.candidates(LIVE_MODEL)
        if self._realtime:
            self._realtime.reset()
        self._live.set_client(None if self._realtime else self.keys.client(candidates[0]) if candidates else None)

    def _show(self, generation, text):
        AppHelper.callAfter(self._show_if_current, generation, text)

    def _show_if_current(self, generation, text):
        if generation == self._generation:
            self._overlay.show_text(self.keys.redact(text))

    def _run(self, generation, started, screen_only, prepared_inputs=None, capture_display_id=None, audio_only=False):
        try:
            if generation != self._generation:
                return
            if prepared_inputs is None:
                screenshot = None if audio_only else self._capture_pool.submit(
                    capture_screenshot, self.capture_display_id if capture_display_id is None else capture_display_id,
                )
                wav = None if screen_only else self._audio.wav_bytes()
                jpeg = screenshot.result() if screenshot is not None else None
            else:
                jpeg, wav = prepared_inputs
        except Exception as e:
            self._show(generation, f"⚠️ Не вдалося підготувати запит: {e}")
            return
        if audio_only and not wav:
            self._show(generation, "Аудіобуфер ще порожній. Повторіть після запису звуку.")
            return
        if audio_only:
            prompt = "Аудіо — останній фрагмент розмови. Екран не додано. Дай коротку відповідь у 1–3 реченнях."
        else:
            prompt = USER_PROMPT if wav else "Зображення — поточний екран. Аудіо не додано."
        contents = [prompt]
        if jpeg is not None:
            contents.insert(0, types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
        if wav:
            contents.insert(0, types.Part.from_bytes(data=wav, mime_type="audio/wav"))

        prepared = time.perf_counter() - started
        models = list(dict.fromkeys((PRIMARY_MODEL, FALLBACK_MODEL)))
        for index, model in enumerate(models):
            for entry in self.keys.candidates(model):
                if entry not in self.keys.candidates(model):
                    continue
                if generation != self._generation:
                    return
                mode = "тільки аудіо" if audio_only else "екран + аудіо" if wav else "тільки екран"
                status = "Резервна модель" if index else "Очікую відповідь"
                self._show(generation, f"⏳ {status} · {model}\n{mode}  ·  «Очистить» — скасувати")
                # Only set thinking levels for models whose capabilities are known.
                level = {"gemini-3.5-flash-lite": "minimal", "gemini-3.8-flash": "low"}.get(model)
                config = types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    thinking_config=types.ThinkingConfig(thinking_level=level) if level else None,
                )
                text = ""
                first_token = None
                last_render = 0.0
                header = f"{model} · {entry.name}\n\n"
                try:
                    with closing(self.keys.client(entry).models.generate_content_stream(
                        model=model, contents=contents, config=config
                    )) as stream:
                        for chunk in stream:
                            if generation != self._generation:
                                return
                            delta = chunk.text
                            if not delta:
                                continue
                            now = time.perf_counter()
                            if first_token is None:
                                first_token = now - started
                                self.keys.succeeded(entry)
                                header = f"{model} · {entry.name} · перший текст {first_token:.2f} с\n\n"
                            text += delta
                            # Render the first text immediately; batch subsequent updates to 25 fps.
                            if now - last_render >= 0.04:
                                self._show(generation, header + text)
                                last_render = now
                    if generation != self._generation:
                        return
                    elapsed = time.perf_counter() - started
                    self._show(generation, header + (text or "(порожня відповідь — ⌥X повторити)")
                               + f"\n\n✓ Завершено за {elapsed:.2f} с")
                    first_label = f"{first_token:.2f}s" if first_token is not None else "none"
                    print(f"[{model}] prepare={prepared:.2f}s first_text={first_label} total={elapsed:.2f}s")
                    return
                except errors.APIError as e:
                    if generation != self._generation:
                        return
                    if credential_error(e):
                        self.keys.failed(entry, model, e)
                        if not text:
                            continue
                    if not text and e.code in (404, 500, 502, 503, 504) and index + 1 < len(models):
                        break
                    self._show(generation, f"{header}{text}\n\n⚠️ {e.code}: {e.message}")
                    return
                except Exception as e:
                    self._show(generation, f"{header}{text}\n\n⚠️ {type(e).__name__}: {e}")
                    return
        self._show(generation, self.keys.unavailable_message())

    def _run_realtime(self, generation, started, audio_only):
        text = ""
        mode = "тільки аудіо" if audio_only else "екран + аудіо"
        first_display = None
        request_monotonic = time.monotonic() - (time.perf_counter() - started)
        try:
            for hint in self._realtime.stream(audio_only, lambda: generation != self._generation):
                if generation != self._generation:
                    return True
                if first_display is None:
                    first_display = time.perf_counter() - started
                    cached = hint.first_text_at < request_monotonic
                    timing = f"готово до натискання · показ {first_display:.2f} с" if cached else (
                        f"перший текст після натискання {first_display:.2f} с")
                    print(f"[realtime] display={first_display:.3f}s cached={cached}")
                    if hint.vad_end_at and hint.first_text_at >= hint.vad_end_at:
                        print(f"[realtime] server_vad_end_to_text={hint.first_text_at-hint.vad_end_at:.3f}s")
                text = hint.text
                footer = "\n\n✓ Готово" if hint.complete else ""
                self._show(generation, f"Live · {self._realtime.model} · {mode}\n{timing}\n\n{text}{footer}")
            return generation != self._generation or bool(text)
        except Exception as error:
            if generation != self._generation:
                return True
            if text:
                self._show(generation, f"Live · {mode}\n\n{text}\n\n⚠️ {error}")
                return True
            print(f"[realtime] using on-demand fallback: {self.keys.redact(error)}")
            return False

    def _run_live(self, generation, started, screen_only=False, capture_display_id=None, audio_only=False):
        if self._realtime and self._realtime.enabled:
            if self._run_realtime(generation, started, audio_only):
                return
        text = ""
        jpeg = pcm = None
        header = f"Live · {'тільки аудіо' if audio_only else 'екран + аудіо'} · {LIVE_MODEL}\n\n"
        try:
            if generation != self._generation:
                return
            screenshot = None if audio_only else self._capture_pool.submit(
                capture_screenshot, self.capture_display_id if capture_display_id is None else capture_display_id,
            )
            pcm = self._audio.pcm_bytes()
            jpeg = screenshot.result() if screenshot is not None else None
            if generation != self._generation:
                return
            if not pcm:
                raise ValueError("Аудіобуфер ще порожній")
            prepared = time.perf_counter() - started
            self._show(generation, header + "⏳ Очікую коротку підказку…  ·  «Очистить» — скасувати")
            for entry in self.keys.candidates(LIVE_MODEL):
                if generation != self._generation:
                    return
                if entry not in self.keys.candidates(LIVE_MODEL):
                    continue
                try:
                    self._live.use_client(self.keys.client(entry))
                    first_text = None
                    last_render = 0.0
                    with closing(self._live.stream(jpeg, pcm, lambda: generation != self._generation)) as stream:
                        for delta in stream:
                            now = time.perf_counter()
                            if first_text is None:
                                first_text = now - started
                                self.keys.succeeded(entry)
                                header = f"Live · {'тільки аудіо' if audio_only else 'екран + аудіо'} · перший текст {first_text:.2f} с\n\n"
                            text += delta
                            if now - last_render >= 0.04:
                                self._show(generation, header + text)
                                last_render = now
                    if generation != self._generation:
                        return
                    elapsed = time.perf_counter() - started
                    self._show(generation, header + text + f"\n\n✓ {elapsed:.2f} с  ·  ⌥X — деталі / код")
                    print(f"[{LIVE_MODEL}] prepare={prepared:.2f}s first_text={first_text:.2f}s total={elapsed:.2f}s")
                    return
                except Exception as error:
                    if credential_error(error):
                        self.keys.failed(entry, LIVE_MODEL, error)
                        if not text:
                            continue
                    raise
            raise ValueError(self.keys.unavailable_message())
        except Exception as error:
            if generation != self._generation:
                return
            if text:
                self._show(generation, header + text + f"\n\n⚠️ {error}  ·  ⌥X — повторити")
            else:
                print(f"[{LIVE_MODEL}] falling back to text stream: {self.keys.redact(error)}")
                prepared_inputs = (jpeg, self._audio.wav_bytes(pcm)) if pcm else None
                self._run(generation, started, screen_only, prepared_inputs, capture_display_id, audio_only=audio_only)


# --- Global hotkeys -----------------------------------------------------------

class Hotkeys:
    """Session-level CGEventTap that swallows Option+Q/Z/X combos. Needs Accessibility permission."""

    def __init__(self, actions):
        self._actions = actions  # keycode -> (callable, fire_on_autorepeat)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
            self._on_event,
            None,
        )
        if self._tap is None:
            sys.exit(
                "Cannot create event tap. Grant Accessibility to your terminal app in "
                "System Settings → Privacy & Security → Accessibility, then restart it."
            )
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)

    def _on_event(self, proxy, event_type, event, refcon):
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        if (Quartz.CGEventGetFlags(event) & MODIFIER_MASK) != HOTKEY_MODIFIERS:
            return event
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if keycode not in self._actions:
            return event
        action, fire_on_autorepeat = self._actions[keycode]
        if fire_on_autorepeat or not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
            try:
                action()
            except Exception as e:
                print(f"Hotkey action failed: {e}", file=sys.stderr)
        return None  # swallow


# --- Main ---------------------------------------------------------------------

def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, not in Cmd+Tab

    try:
        keys = KeyPool(KeychainStore(), os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    except KeychainError as error:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Не удалось открыть API-ключи")
        alert.setInformativeText_(str(error))
        alert.runModal()
        return
    preferences = Preferences()
    overlay = Overlay()
    audio = AudioRing()
    assistant = Assistant(overlay, audio, keys)
    controls = Controls.alloc().initWithActions_({
        "streaming": assistant.toggle_realtime,
        "audio": lambda: assistant.analyze(live=True, audio_only=True),
        "live": lambda: assistant.analyze(live=True),
        "answer": assistant.analyze,
        "screen": lambda: assistant.analyze(screen_only=True),
        "clear": assistant.clear,
        "up": lambda: overlay.scroll(-SCROLL_STEP),
        "down": lambda: overlay.scroll(SCROLL_STEP),
    })
    controls.configure(preferences, overlay, audio, assistant)
    assistant.start_realtime(lambda: AppHelper.callAfter(controls.update_status))
    hotkeys = Hotkeys({  # noqa: F841 — must stay referenced while the run loop runs
        KEY_X: (assistant.analyze, False),
        KEY_Z: (lambda: assistant.analyze(live=True), False),
        KEY_Q: (lambda: assistant.analyze(live=True, audio_only=True), False),
    })

    print(f"Recording (last {preferences.audio_seconds}s sent). Models: {PRIMARY_MODEL} → {FALLBACK_MODEL}")
    print("⌥Q audio only · ⌥Z Live hint · ⌥X text/code · Ctrl+C quit")
    controls.show()
    overlay.show_text("Готов к работе.\n\nТолько звук — ⌥Q\nПодсказка — ⌥Z\nОтвет / код — ⌥X\nАудіо та екран передаються у Gemini в фоні.\nКнопка «Поток: пауза / пуск» зупиняє фонову передачу.\n\n"
                      "Окно можно перемещать за стандартный заголовок сверху.\n"
                      "Мониторы и длительность аудио выбираются в настройках.", reset_scroll=True)
    if not keys.entries():
        controls.openKeys_(None)
    # sharedApplication() already exists, so runEventLoop won't install this itself.
    AppHelper.installMachInterrupt()
    try:
        AppHelper.runEventLoop(installInterrupt=True)
    finally:
        assistant.close()


if __name__ == "__main__":
    main()
