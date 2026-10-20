# Interview assistant

Текущая версия: **v0.2.0 — непрерывная подготовка подсказок**.
Замеры и ограничения: [LATENCY.md](LATENCY.md).

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
**Очистить**, **Пауза потока / Включить поток**, **↑ / ↓** and **Настройки…**. Keyboard shortcuts are written directly
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
| ⌥Q (Option + Q) | reveal the latest continuously prepared audio-only hint |
| ⌥X (Option + X) | detailed text/code answer from recent audio + selected screenshot |
| ⌥Z (Option + Z) | reveal the latest continuously prepared audio + screen hint |

These are the three global shortcuts. Use **Только экран** to send a screenshot
without audio, **Очистить** to cancel and clear, and **↑ / ↓** or the mouse/trackpad
to scroll. The shortcuts work while another app is focused.
**Только звук** uses the current audio input. It does not automatically mix
microphone and system audio. Its model session receives no images. The screen-aware
session is separate, so switching between ⌥Q and ⌥Z does not reconnect either one.

Continuous mode starts with the app: **audio is sent to Gemini while people speak**,
and the selected screen is captured/compressed/uploaded every **1.5 seconds**.
There are two independent Live sessions to keep the audio-only context free of
images. This uses API quota even before a hotkey is pressed. The toolbar shows
connection state. **Пауза потока** stops background transmission and disconnects
both sessions; **Включить поток** resumes with fresh contexts. While paused, the
hotkeys still explicitly send on-demand requests as before.

The continuous input uses **20 ms / 320-frame** PCM chunks, bounded to one second
of queued audio. A slow/disconnected network discards that unsafe context and
reconnects instead of accumulating delay. Server VAD is configured for **350 ms**
of silence. A keypress during a quiet tail also queues `audio_stream_end` after
the already captured PCM (hybrid VAD), so it can finalize without waiting the full
350 ms. If speech has resumed, that flush is skipped. A low-energy onset check only invalidates stale cached text early;
the server still decides turn endings. If the transcript includes an input stream
interruption, the old answer is not concatenated with the new question.

Screenshots use integer reduction, bilinear scaling and JPEG quality 60 with a
100 KB target. The model uses `MEDIA_RESOLUTION_LOW`. A significant change in the
screen invalidates the previous cached answer; the next press uses a fresh
on-demand request if no new spoken question has produced a replacement. Tiny
cursor/caret/antialiasing changes are ignored by the visual fingerprint. Changing
the selected display immediately clears the screen context and old frame cache.

If an answer was ready before the keypress, the overlay says so and reports the
time to display it. That is **not** model generation latency. New answers report
time from keypress to first text. Terminal timings include connection setup,
background capture/JPEG size and, where the API supplies it, server speech-end to
first transcript event. Input transcript arrival is not treated as an exact
speech-end timestamp. See [LATENCY.md](LATENCY.md) for actual measurements.

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
Up to 60 seconds are kept locally in RAM. The selected recent duration applies
to ⌥X and on-demand fallback; continuous Live hints receive audio as it arrives
instead of replaying that buffer. Increasing the duration can use audio already
retained since launch.

Fast hints default to `gemini-3.1-flash-live-preview` with
`thinking_level="minimal"`, based on the local synthetic latency probes in
[LATENCY.md](LATENCY.md). Set `GEMINI_LIVE_MODEL=gemini-3.8-live` to use the prior
model; Google requires omitting `thinking_level` for 3.8 Live, and the app does so.
Gemini 2.5 uses `thinking_budget=0`.
These options are model-specific ([Google documentation](https://ai.google.dev/gemini-api/docs/live-api/capabilities)).

Live hints show output audio transcription without playing audio. Use ⌥X for
formatted text and code. Background disconnects, expired sessions and quota errors
reconnect using eligible keys; permission failures remain scoped to the model.
The continuous path waits at most **0.8 seconds** for an idle stream with no text,
or **2.5 seconds** for a known in-flight question, before an on-demand fallback.
A not-ready/failed session falls back immediately. No request is replayed after
text has been shown. The on-demand Live fallback still uses manual activity
boundaries with a 5-second connection/first-transcript timeout and 45-second turn
cap; REST fallback preserves the same screen/audio snapshot. Audio-only fallbacks
never capture or send the screen. **Очистить** cancels the visible request, clears
the ring buffer and starts fresh background contexts if continuous mode is enabled.

Text/code defaults: `gemini-3.5-flash-lite` with minimal thinking, falling back to
`gemini-3.8-flash` with low thinking when available keys are exhausted by rate limits, or on
unavailable models or transient server errors before any answer text arrives. SDK retries are disabled so the
fallback starts immediately after these errors; network operations time out after
20 seconds. Set `GEMINI_MODEL=gemini-3.8-flash` to favor stronger reasoning over speed.

The first answer text is displayed as soon as it arrives. On-demand responses show
time from hotkey press to first text and total completion time; terminal logs also
show local preparation time. Responses stay at the top for reading. Scroll down to the
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
