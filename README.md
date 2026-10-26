# SOBES — Windows и macOS

Голосовой помощник с контекстом файлов и экрана на Gemini. Этот форк добавляет Windows-интерфейс и платформенные реализации, сохраняя исходный режим macOS.

## Запуск на Windows

Нужна Windows 10/11 и **64-битный Python 3.11–3.13** с Tcl/Tk (обычная установка с [python.org](https://www.python.org/downloads/windows/)). Python 3.12 уже установлен на машине, на которой подготовлен этот форк.

1. Скачайте или клонируйте этот репозиторий.
2. Дважды нажмите **`Запустить.cmd`** (или `Start-Windows.cmd`). При первом запуске скрипт создаст `.venv` и установит зависимости. Нужен интернет; Homebrew, Xcode и виртуальный аудиокабель не нужны.
3. В окне **API-ключи Gemini** добавьте свой ключ из [Google AI Studio](https://aistudio.google.com/) и нажмите **Сохранить и использовать**. Можно хранить несколько профилей, редактировать их, удалять и включать автоматическое переключение.
4. В **Настройки…** выберите **Микрофон** или **Звук компьютера**, три экрана независимо (снимки, ответ, кнопки) и длительность аудиобуфера. Нажмите **Применить**.

Глобальные клавиши работают и при фокусе в другом приложении:

| Клавиши | Действие |
| --- | --- |
| Alt+A | Вопрос голосом по выбранным файлам |
| Alt+Q | Вопрос голосом с историей последних шести пар вопрос/ответ |
| Alt+Z | Короткая подсказка по свежему снимку экрана и недавнему аудио |
| Alt+X | Подробный ответ / код по экрану и аудио |

Кнопки **Только экран**, **Очистить**, **Пауза потока**, прокрутка и полное завершение доступны на панели. Окна перемещаются за заголовок, положения сохраняются. Если сочетание занято другой программой, панель покажет сообщение, а соответствующая кнопка останется доступной.

**Звук компьютера** записывает текущее устройство вывода через WASAPI loopback, включая звук в наушниках. Оно не смешивается с микрофоном. После смены наушников или устройства вывода снова нажмите **Применить**. При ошибке устройство не подменяется микрофоном. Захват обоих каналов преобразуется в PCM16 mono 16 кГц; речь только в правом канале сохраняется.

**Файлы…** поддерживает добавление нескольких документов, выбор файлов из папки и удаление из контекста без удаления оригиналов. PDF читаются через `pypdf`, DOCX и исходники — общей логикой приложения. Лимиты и ссылки на страницы/строки совпадают с исходной версией; сканам нужен OCR.

Настройки хранятся в `%LOCALAPPDATA%\InterviewAssistant\settings.json` в UTF-8. Ключи — отдельно в `api-keys.dpapi`, зашифрованы Windows DPAPI для текущего пользователя; перенос этого файла на другой профиль Windows не переносит ключи. В репозиторий ключи не записываются. Окна ответа и кнопок используют `SetWindowDisplayAffinity` для исключения из совместимого захвата на Windows 10 2004+; это не гарантия для всех программ записи. Если Windows отклонит настройку, панель сообщит об этом.

При включённом потоке аудио отправляется Gemini в фоне. **Пауза потока** останавливает фоновую передачу, но явные запросы кнопками остаются доступны. Скриншоты отправляются только по запросу. Работа моделей зависит от доступности API, региона, проекта и квоты; перенесены исходные настройки моделей и резервные цепочки. Их можно переопределить переменными окружения, перечисленными ниже.

Ручной запуск из PowerShell после установки зависимостей:

```powershell
.\.venv\Scripts\python.exe -X utf8 assistant.py
```

Локальные проверки без обращения к Gemini (тесты Windows на короткое время создают тестовые окна, реальные микрофон и loopback не записываются):

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Если нет звука микрофона, разрешите доступ настольных приложений к микрофону в настройках конфиденциальности Windows. Для системного аудио проверьте устройство вывода. Если выбранный экран отключён, снимок блокируется до выбора подключённого экрана. Закрытие панели кнопок останавливает аудио, поток и горячие клавиши; закрытие окна ответа только скрывает его.

Проверено локально на Windows / Python 3.12: установка, Tk-окна и диалоги, микрофон, WASAPI loopback, JPEG-снимок, регистрация горячих клавиш, шифрование DPAPI и офлайн-тесты. Живой ответ Gemini требует собственного API-ключа и при подготовке Windows-порта не проверялся. CI проверяет Python 3.12 и 3.13 на Windows и macOS.

## Исходная версия и запуск на macOS

Основная версия: **v0.5.0 — быстрый голосовой диалог и несколько API-ключей**.

Запуск: **Запустить.command** (двойной щелчок) или `.venv/bin/python assistant.py`. ⌥Q использует потоковое распознавание со словарём и Flash-Lite minimal. Сохраняются последние шесть вопросов/ответов, поэтому работают уточнения «добавь к нему…» и «чем заменить этот вызов». «Очистить» сбрасывает диалог. Распознанный вопрос виден над ответом; новая речь продолжает записываться.

При нескольких доступных ключах STT остаётся на своём соединении, а текстовые первичные попытки чередуются между остальными профилями. Если первых букв нет за 350 мс, запускается вторая попытка; её текст показывается только если она выиграла. После первых букв источник не меняется. Это **может расходовать вдвое больше текстовых запросов** и не обещает увеличения общей квоты: Google ограничивает проект, а не ключ. Отключение автосмены ключей оставляет только выбранный профиль.

Финальные проверки: **8/8 правильных диалоговых ответов**, медиана первого текста **0,93 с**, диапазон **0,80–1,39 с**. Нативный захват воспроизводимой речи на Mac дал **0,89–0,99 с**. Beta1 без истории была быстрее по медиане (0,77 с), но дала только 5/8 конкретно правильных ответов. Тест не гарантирует скорость любого живого звонка. Все варианты и ограничения: [отчёт о диалоге и задержке](benchmarks/dialog_latency/REPORT.md).

`Тестовая.command` / `test_version.py` оставлены как совместимые способы запуска того же голосового режима; одновременно запускайте только один экземпляр. Файловый и экранный режимы используют свои модели. Если STT недоступен, используется PCM, сохранённый при нажатии, через Flash-Lite → 3 Flash Preview. Старый Live-ответ не подменяет текущий вопрос.

Файловые тесты: [5 Python-модулей, 754 строки, вопросы преподавателя и шум](benchmarks/files_project/REPORT.md).
Полный сравнительный тест: [45 API-идентификаторов из каталога 63 моделей](benchmarks/REPORT.md).
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
are skipped; model permission/unavailability failures (403/404) only block that model for that key in the running process. A project-specific 404 tries other keys before another model; a partial answer is preserved without replay. Clearing the answer
does not reconnect keys that are unavailable. Remaining quota is not predicted.

Google applies [rate limits per project, not per API key](https://ai.google.dev/gemini-api/docs/rate-limits).
Keys from the same project share its quota. Optionally enter the same Google Cloud
project identifier for such keys so the app can put them on cooldown together.
Without this label, each key may be tried once before its quota error is known.
If no eligible key remains, the app reports the limit or uses the configured
fallback model where available. Turn off the checkbox to use only the active key.
`GEMINI_API_KEY` remains an optional import for existing setups; save that profile
in the window once to keep it across launches without the environment variable.

The floating toolbar provides **Вопрос по файлам**, **Файлы…**, **Только звук**, **Подсказка**, **Ответ / код**, **Только экран**,
**Очистить**, **Пауза потока / Включить поток**, **↑ / ↓**, **Настройки…** and **Закрыть полностью**. Keyboard shortcuts are written directly
on the buttons. Both the toolbar and answer window move by dragging their native
macOS title bars. Positions are saved. The answer accepts mouse/trackpad scrolling.

Перетаскивание заголовков выполняет macOS; сохранение положения и обновление
панели откладываются до паузы в движении, чтобы не нагружать каждое перемещение.

Open **Настройки…** (or **⌘,** while the app is focused) to choose independently:

- the display for the answer window;
- the display for the toolbar;
- the display to capture and send to Gemini;
- the audio source: microphone or direct computer audio (calls/videos);
- the recent audio duration: 8, 15, 30, 45 or 60 seconds, default **30 seconds**.

Press **Применить** to save and apply changes without restarting. The toolbar always
shows all three display choices, the selected audio duration and the input device.
Settings also provide **↑ Выше**, **↓ Ниже** and **Сбросить положение** for the answer
window. Quit using **Закрыть полностью** in the main window, **Завершить** in settings, **⌘Q**, or **Ctrl+C** in the terminal.

Preferences are stored in `~/Library/Application Support/InterviewAssistant/settings.json`;
no API key is stored there. An unplugged capture display blocks capture and is shown
as disconnected; the app does not silently send another screen. Disconnected output
windows are brought back to an available display.

| Hotkey (two keys together) | Action |
|---|---|
| ⌥A (Option + A) | answer a spoken question using selected context files, without a screenshot |
| ⌥Q (Option + Q) | finalize the spoken question and answer using the last six dialog turns |
| ⌥X (Option + X) | detailed text/code answer from recent audio + selected screenshot |
| ⌥Z (Option + Z) | short hint from a fresh screenshot + compressed recent audio, separate REST request |

These are the four global shortcuts. Use **Только экран** to send a screenshot
without audio, **Очистить** to cancel and clear, and **↑ / ↓** or the mouse/trackpad
to scroll. The shortcuts work while another app is focused.
**Только звук** uses the source selected in settings. It does not mix microphone and system audio. There is exactly **one warm audio-only Live session**.
No image is ever sent to it. ⌥Z and ⌥X make independent REST requests with a fresh
screenshot and the recent audio buffer; they do not reset or contaminate the voice
context. ⌥Z requests a short hint; ⌥X requests the complete explanation/code.

### Вопрос голосом по файлам

1. Нажмите **Файлы… → Добавить…** и выберите материалы.
2. Задайте вопрос вслух сами или попросите человека рядом. Нужна слышимость через выбранный аудиовход; распознавания конкретного говорящего нет.
3. После вопроса нажмите **⌥A** или **Вопрос по файлам**. Ответ появится со ссылками на страницы PDF или строки документа и списком источников F1/F2.

В окне **Файлы…** можно добавлять несколько файлов за раз. **Из папки…**
показывает поддерживаемые файлы из папки и подпапок: отметьте нужные строки или
нажмите **Выбрать все**, затем **Добавить выбранные**. Скрытые файлы и папки,
`venv`, `node_modules`, `__pycache__` и символические ссылки пропускаются.
Большие деревья (более 5000 просмотренных записей) требуют выбора конкретной
подпапки. Из папки ничего не добавляется без выделения и нажатия кнопки.

В списке контекста выделяйте несколько строк через **⌘ / Shift**, используйте
**Убрать выбранные** или **Убрать все**. Эти действия меняют только контекст;
исходные файлы остаются на диске. Лимит — 20 файлов, дубликаты не добавляются.

Вопрос берётся из последних секунд аудиобуфера, выбранных в настройках. Для длинных вопросов поставьте 30–60 секунд. Звук фиксируется при нажатии, поэтому чтение документов не вытесняет вопрос из буфера. Микрофон и звук динамиков автоматически не смешиваются.

Поддерживаются текстовые PDF, DOCX и текстовые файлы (TXT, Markdown, CSV, JSON, исходный код и другие форматы, перечисленные в `file_context.py`). DOCX: текст основной части и таблиц; без колонтитулов и сносок. PDF со страницами без извлекаемого текста требует OCR; приложение укажет такие страницы. Изображения документов не анализируются.

Лимиты: до 20 файлов, до 300 000 символов совокупного JSON-контекста; текстовый файл до 2 МБ, PDF/DOCX до 20 МБ, PDF до 1000 страниц. При превышении лимита, удалении или невозможности прочитать файл показывается ошибка; материалы не обрезаются молча. Для большого архива выберите нужные документы: отдельного поискового индекса пока нет.

Список путей сохраняется между запусками. Извлечённый текст кешируется в памяти и перечитывается после изменения файла. Полный текст выбранных материалов и аудиовопрос отправляются Gemini только при **⌥A**, отдельным запросом без скриншота. Удаление из списка не удаляет оригинал. Другие режимы не используют эти файлы.

Этот режим использует `gemini-3.5-flash-lite` с **high thinking**, с резервом `gemini-3.8-flash` low. В тесте по пяти модулям прежний low дал четыре ошибки основной логики из девяти вопросов; high с уточнённой инструкцией прошёл 9 обычных и 7 тихих/зашумлённых вопросов. Медианы до первого текста — 3,33 и 3,92 с соответственно; это не гарантия для любого проекта. Демо: `benchmarks/files_project/`, выбирайте только models.py, pricing.py, storage.py, service.py и reporting.py. Тайм-аут до первых слов допускает переход на резерв; после начала ответа повторной отправки нет. Для обработки документов тайм-аут отдельного запроса увеличен с 20 до 60 секунд. Скорость зависит от объёма материалов и API; это не обещание ответа за секунду. При отсутствии ответа в документах модель получает указание сказать об этом явно; ссылки помогают проверить результат.

Для исходников передаются все строки, включая пустые, с номерами от 1, как в
VS Code. Ссылки имеют вид `[F1: service.py, рядок 55]`. Перед вопросом сохраните
файл в редакторе (⌘S): следующий запрос подхватит текущую версию на диске.
Перенос длинной строки на экране не создаёт нового номера строки.

### Звук звонка или видео

Откройте **Настройки… → Что слушать → Звук компьютера → Применить**. Теперь ⌥Q получает голос собеседника напрямую, включая работу в наушниках. Этот источник также используется для аудиобуфера ⌥A/⌥Z/⌥X. Микрофон в этом режиме не записывается: чтобы задать вопрос своим голосом, выберите «Микрофон».

Источник сохраняется между запусками и показывается на панели. Переключение очищает буфер, отменяет старый ответ и начинает свежий Live-контекст. Длительность 8–60 секунд влияет на REST-буфер, а не на непрерывный Live-поток ⌥Q.

На macOS 14.2+ звук компьютера захватывается через [Core Audio process tap](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps), на старых версиях — через ScreenCaptureKit. При преобразовании в mono смешиваются оба канала: это важно для Discord, где в проверенном звонке речь шла только справа. BlackHole не нужен, системное устройство вывода не меняется. При запросе macOS разрешите запись системного аудио. Helper передаёт только PCM 16 кГц mono, без кадров экрана. Захватывается общий звук приложений, включая другие открытые видео и уведомления; для чистого вопроса остановите лишнее воспроизведение. Защищённый контент может быть недоступен для захвата.

Первый запуск собирает небольшой Swift helper в фоне (нужны Command Line Tools: `xcode-select --install`). Пока он подключается или если разрешение отклонено, запрос покажет состояние/ошибку. После выдачи разрешения повторно нажмите «Применить». Если macOS временно возвращает пустой список дисплеев, приложение ждёт в фоне и запускает захват после появления дисплея. Ошибки разрешения и ручная остановка системной трансляции автоматически не обходятся. Автоматического возврата к микрофону нет. Проба «системный тестовый звук → Gemini Live» завершилась правильным полным ответом; распознавание реального звонка ещё нужно проверить на вашей речи.

Continuous audio starts with the app: **audio is sent to Gemini while people speak**.
Screenshots are now captured and sent **only on explicit screen requests** (⌥Z,
⌥X, or **Только экран**). No background screen upload or second Live session remains.
**Пауза потока** disconnects the voice session; **Включить поток** resumes with a
fresh context. While paused, the hotkeys still explicitly send on-demand requests.

Системный захват выдаёт 20-мс фреймы PCM16 mono 16 кГц. Распознавание получает их пакетами по 100 мс, очередь ограничена шестью секундами. ⌥Q немедленно отправляет оставшийся хвост и фиксирует границу вопроса. Финальная расшифровка вместе с шестью предыдущими парами вопрос/ответ идёт в Flash-Lite minimal. Новая речь продолжает поступать и не прерывает показ ответа. «Очистить» отменяет текущий запрос и сбрасывает историю.

For REST, the recent PCM is encoded **in memory as OGG/Opus, 24 kbps**, using
ffmpeg's fastest complexity setting. Only digital silence at the edges is trimmed,
with 300 ms padding; quiet words and interior pauses stay intact. Very short clips
use WAV. If ffmpeg is missing, fails, or exceeds 500 ms, the same trimmed PCM is
sent as WAV instead. Optional encoder installation: `brew install ffmpeg`.
No audio files or transcripts are saved. The terminal reports input/sent duration,
payload size and preparation time, without printing the conversation.

Время первого текста считается от нажатия ⌥Q; оно включает финализацию распознавания и ожидание первого текстового фрагмента. Замеры: [отчёт](benchmarks/dialog_latency/REPORT.md).

Grant your terminal app **Microphone**, **Screen Recording** and **Accessibility** in
System Settings → Privacy & Security, then restart the terminal.

Optional env vars: `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, `GEMINI_LIVE_MODEL`, `GEMINI_AUDIO_MODEL`, `GEMINI_AUDIO_FALLBACK_MODEL`, `GEMINI_FILES_MODEL`, `GEMINI_FILES_FALLBACK_MODEL`,
`SOBES_AUDIO_PIPELINE` (`transcript` по умолчанию; `legacy` — прежняя Live-цепочка),
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
to ⌥Z, ⌥X and on-demand fallback; streaming transcription receives audio as it arrives
instead of replaying that buffer. Increasing the duration can use audio already
retained since launch.

Основной ⌥Q использует `gemini-3.5-transcribe-live` → `gemini-3.5-flash-lite` minimal. При сбое распознавания отправляется сжатое аудио, зафиксированное при нажатии, в Flash-Lite minimal → `gemini-3-flash-preview` minimal. Тайм-аут голосового REST-вызова — 10 с. После появления текста ответ не переотправляется. Экранные запросы и вопросы по файлам используют свои модели. Прежний режим доступен через `SOBES_AUDIO_PIPELINE=legacy .venv/bin/python assistant.py`; его старые замеры описаны в [LATENCY.md](LATENCY.md).

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

By default, existing installations keep microphone input. Select direct computer audio in settings for calls/videos. `AUDIO_DEVICE` only selects a microphone/PortAudio input in microphone mode; it does not override the saved source.
