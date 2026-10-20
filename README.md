# Interview assistant

**v0.1.0 — первая рабочая версия.** В пользовательском тесте первый текст
появился примерно через **3,5 секунды** после нажатия горячей клавиши.
Это отдельное наблюдение, не средняя задержка. Подробности: [CHANGELOG.md](CHANGELOG.md).

On this Mac, double-click **Запустить.command** in Finder to launch. On first launch,
the **API-ключи Gemini** window opens automatically. Paste a key there and click
**Сохранить и использовать**. No Terminal key entry is required.

```sh
brew install portaudio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python assistant.py
```

Open **Настройки… → API-ключи…** to add, name, edit or delete credentials.
Saved secrets are hidden and stored in the macOS Keychain, separately from ordinary
preferences. Leave the secret field blank when editing to keep the existing key.
Use **Добавить ключ** for another credential; **Сохранить и использовать** makes it
active. The toolbar shows the active profile and whether automatic switching is on.
Get Gemini API keys in [Google AI Studio](https://aistudio.google.com/apikey).

With automatic switching enabled, a quota response (429) before the first answer
text makes the app try the next available key. It respects the server's retry delay
and does not replay a question after text has already arrived. Invalid credentials
are skipped; model permission failures only block that model. Clearing the answer
does not reconnect keys that are unavailable. Remaining quota is not predicted.

Google applies [rate limits per project, not per API key](https://ai.google.dev/gemini-api/docs/rate-limits).
Keys from the same project share its quota. Optionally enter the same Google Cloud
project identifier for such keys so the app can put them on cooldown together.
Without this label, each key may be tried once before its quota error is known.
If no eligible key remains, the app reports the limit or uses the configured
fallback model where available. Turn off the checkbox to use only the active key.
`GEMINI_API_KEY` remains an optional import for existing setups; save that profile
in the window once to keep it across launches without the environment variable.

The floating toolbar provides **Только звук**, **Подсказка**, **Ответ / код**, **Только экран**,
**Очистить**, **↑ / ↓** and **Настройки…**. Keyboard shortcuts are written directly
on the buttons. Both the toolbar and answer window move by dragging their native
macOS title bars. Positions are saved. The answer accepts mouse/trackpad scrolling.

Open **Настройки…** (or **⌘,** while the app is focused) to choose independently:

- the display for the answer window;
- the display for the toolbar;
- the display to capture and send to Gemini;
- the recent audio duration: 8, 15, 30, 45 or 60 seconds, default **30 seconds**.

Press **Применить** to save and apply changes without restarting. The toolbar always
shows all three display choices, the selected audio duration and the input device.
Settings also provide **↑ Выше**, **↓ Ниже** and **Сбросить положение** for the answer
window. Quit using **Завершить** in settings, **⌘Q**, or **Ctrl+C** in the terminal.

Preferences are stored in `~/Library/Application Support/InterviewAssistant/settings.json`;
no API key is stored there. An unplugged capture display blocks capture and is shown
as disconnected; the app does not silently send another screen. Disconnected output
windows are brought back to an available display.

| Hotkey (two keys together) | Action |
|---|---|
| ⌥Q (Option + Q) | short Live hint from recent audio only; no screen capture or image upload |
| ⌥X (Option + X) | detailed text/code answer from recent audio + selected screenshot |
| ⌥Z (Option + Z) | short Live hint from recent audio + selected screenshot |

These are the three global shortcuts. Use **Только экран** to send a screenshot
without audio, **Очистить** to cancel and clear, and **↑ / ↓** or the mouse/trackpad
to scroll. The shortcuts work while another app is focused.
**Только звук** uses the same selected audio input and recent duration as the other
audio modes; it does not automatically mix microphone and system audio. It skips
screen capture, including REST fallback. Switching between audio-only and screen
Live modes starts a fresh session so old images are not used for an audio-only hint.
Consecutive audio-only hints reuse the connection. Compare the displayed time to
first text with the v0.1.0 observation of approximately 3.5 seconds; no new latency
claim has been measured yet.

Grant your terminal app **Microphone**, **Screen Recording** and **Accessibility** in
System Settings → Privacy & Security, then restart the terminal.

Optional env vars: `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, `GEMINI_LIVE_MODEL`,
`AUDIO_BUFFER_SECONDS` (1–60, default 30), `AUDIO_DEVICE` (input device name substring),
`MONITOR` (display number shown in settings, default 1). Explicit audio/monitor
environment variables override the corresponding saved choice on startup. Use the
settings window for changes while the app is running.

Why 30 rather than 8 seconds? Eight seconds was a speed/context tradeoff, not an API
requirement. At 16 kHz mono PCM, 8 seconds is about 256 KB; 30 seconds is about 960 KB.
Less audio can reduce upload and processing time, but can cut off the question.
It does not make model latency proportionally faster. The app already has the audio
in memory, so choosing 30 seconds does **not** mean waiting 30 seconds after clicking.
Up to 60 seconds are kept locally in RAM; only the selected recent duration is sent
on request. Increasing the duration can use audio already retained since launch.

The `gemini-3.8-live` WebSocket connects at startup and is reused for Live hints.
Audio remains local until a hotkey is pressed. Screenshot capture runs in parallel
with audio preparation. Live receives raw 16 kHz PCM with explicit activity start/end
signals, so it need not wait for automatic silence detection. The default Live model
does not accept a thinking-level override, so that parameter is omitted.

Live hints show the output audio transcription without playing audio. They are short
explanations; use ⌥X for formatted text and code. Native Live models require
audio output and expose text through transcription ([Google documentation](https://ai.google.dev/gemini-api/docs/live-api/capabilities)).

If Live fails before the first text, the app uses the regular text stream with the
same captured screen and audio. Connection setup and the wait for the first
transcript each have a 5-second timeout; a Live turn is capped at 45 seconds.
Disconnect notices are handled while idle, and failed/expired sessions are reopened
ahead of the next hint when possible. **Очистить** cancels the active turn and starts a fresh
Live session, clearing its conversation context as well as the local audio buffer.

Text/code defaults: `gemini-3.5-flash-lite` with minimal thinking, falling back to
`gemini-3.8-flash` with low thinking when available keys are exhausted by rate limits, or on
unavailable models or transient server errors before any answer text arrives. SDK retries are disabled so the
fallback starts immediately after these errors; network operations time out after
20 seconds. Set `GEMINI_MODEL=gemini-3.8-flash` to favor stronger reasoning over speed.

The first answer text is displayed as soon as it arrives. The overlay shows time
from hotkey press to first text and total completion time; terminal logs also show
local preparation time. Responses stay at the top for reading. Scroll down to the
bottom with the **↓** button to follow incoming text; **↑** stops following.

**0.5–1 second to first text** is a target, not a measured guarantee: model load,
network latency, audio upload, input processing and output transcription all
contribute. The immediate loading indicator is not an answer. Use **Только экран** when audio
context is unnecessary. A full code answer can take longer even when the first text
appears quickly. The user reported approximately 3.5 seconds to first text in an initial working
test; the mode, model and network conditions were not recorded. The unit tests
use mock responses and do not benchmark Gemini.

Run the local checks without recording audio or contacting Gemini:

```sh
.venv/bin/python -m unittest -v
```

The default input is the microphone. With headphones on, the mic won't pick up the interviewer.
To capture the call audio instead, install BlackHole (`brew install blackhole-2ch`), create a
Multi-Output Device in Audio MIDI Setup (headphones + BlackHole), set it as the system output,
and run with `AUDIO_DEVICE="BlackHole"`.
