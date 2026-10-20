"""Build a readable audit from saved raw results; no API calls."""
import collections
import importlib.metadata
import json
import platform
import statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parent
inventory=json.loads((ROOT/'model_inventory.json').read_text())['models']
files=list(ROOT.glob('*.jsonl'))
all_rows=[dict(json.loads(line),result_file=file.name) for file in files for line in file.read_text().splitlines() if line.strip()]

def table(file,heading):
    rows=[r for r in all_rows if r['result_file']==file]
    lines=[f'## {heading}','', '| Модель | Непустые завершённые / попытки | Первый текст: медиана | Диапазон | Полный ответ: медиана |', '|---|---:|---:|---:|---:|']
    for model in sorted({r['model'] for r in rows}):
        group=[r for r in rows if r['model']==model]
        successful=[r for r in group if r['status']=='ok' and r.get('first_text_s') is not None]
        times=[r['first_text_s'] for r in successful]
        total=[r['total_s'] for r in successful if r.get('total_s') is not None]
        lines.append(f"| `{model}` | {len(successful)}/{len(group)} | {statistics.median(times):.3f} с | {min(times):.3f}–{max(times):.3f} с | {statistics.median(total):.3f} с |" if times and total else f'| `{model}` | 0/{len(group)} | — | — | — |')
    return '\n'.join(lines)

report='''# Только звук: сравнение доступных моделей

20 октября 2026. Запрос пользователя: найти самый быстрый надёжный ответ на голосовой вопрос, протестировать все доступные варианты без computer use.

**Результат для текущего приложения: сохранить `gemini-3.1-flash-live-preview` для Live; заменить REST-резерв ⌥Q с 3.5 Flash-Lite/3.8 Flash на `gemini-2.5-flash` без thinking, затем `gemini-3-flash-preview` minimal.** Это выбор по короткому локальному тесту, а не универсальное первенство модели.

## Что доступно

В Связке ключей приложения обнаружен один Gemini-профиль. API Models.list вернул **63 идентификатора**, включая алиасы и специализированные модели. Других ключей провайдеров в известных переменных окружения нет. Доступность в списке не означает разрешённый вызов: ряд моделей вернул quota=0, 404 или неподдерживаемую аудиомодальность.

Выполнены аудиозапросы к **36 REST-идентификаторам и 9 Live-идентификаторам** (45 идентификаторов суммарно). Остальные 18 — озвучка, музыка, эмбеддинги, видео и специализированный generateAnswer, не отвечающие на голосовой вопрос этим способом. Для них ниже приведена причина исключения. Имена моделей с computer-use в этом отчёте — только имена в каталоге и API-пробы с синтетическим аудио; управление компьютером, UI и инструменты computer use не использовались.

## Как измеряли

- macOS `say`, голос Milena; три русских вопроса: список/кортеж Python, смена вопроса с HTTP на 17+25, назначение кеша. Файлы 16 кГц mono PCM16, длительности 3.87/8.41/3.32 с. Точный текст, SHA256 и формат — `audio_manifest.json`; генератор — `make_audio.py`.
- Ни микрофон, ни реальный звонок, ни экран, ни пользовательские файлы не отправлялись. На время бенчмарка рабочее приложение остановлено.
- REST: весь голосовой файл кодируется Opus как в приложении; первый текст включает подготовку, сеть и модель. 20-секундный тайм-аут, один HTTP-attempt. Первый широкий проход — 2 запроса одновременно; повторная REST-серия — 1. Часть серий Live и REST шла параллельно: результаты отражают один временной интервал, не лабораторно изолированную нагрузку.
- Live: тёплая сессия, PCM по 20 мс в реальном времени, VAD 350 мс, `NO_INTERRUPTION`, минимальный поддерживаемый thinking. Время от конца отправленного WAV до первой выходной текстовой транскрипции; setup измерен отдельно. «Полный ответ» включает генерацию аудио, хотя приложение показывает только текст: этот хвост может быть заметно длиннее первого текста.
- Главный контроль ⌥Q вызывает настоящие `Assistant._run_live → RealtimeHints.stream`, с тайм-аутами приложения. Программное нажатие имитируется после последнего PCM-чанка, без управления UI. В обеих моделях все 6 ответов пришли из Live, REST не понадобился.
- Нельзя сравнивать задержку отображения заранее готового кеша с генерацией: такие кеш-хиты не использовались в итоговых 6 запросах ⌥Q.
- Успех в числовых таблицах означает непустой завершённый ответ, не экспертную оценку качества. Начальная keyword-проверка давала ложный fail на фразах «кортежі — ні»; сырой `correct` сохранён, но решение опирается на чтение ответов. На 6 контролях ⌥Q обе модели передали правильную основную мысль/42/назначение кеша. Обобщения о скорости кортежей не являются строгой гарантией для всех операций.

'''
report+=table('option_q.jsonl','Главный контроль: реальный путь ⌥Q, 6 вопросов на модель')+'\n\n'
report+=table('app_reserve.jsonl','Проверка нового REST-резерва через код приложения')+'\n\n'
report+=table('rest_finalists.jsonl','Повторная серия REST, тот же аудиовход')+'\n\n'
report+='''Медианы считаются только по успешным ответам. Ошибки не скрыты: число попыток указано отдельно. После ошибки некоторые ранние серии прекращались; это не доказательство неспособности ответить на остальные вопросы. У 3.6 Flash один ответ на 17+25 оборвался на «17 плюс 25 дорівнює», несмотря на серверный STOP; он остаётся завершённым технически, но неверным по смыслу. Робототехническая модель дала ответы, но не быстрее общих моделей и не выбрана для общего голосового помощника.

'''
report+=table('live_continuous.jsonl','Live: непрерывный PCM после конца вопроса, 3 вопроса')+'\n\n'
report+='''В этой таблице `gemini-3.5-transcribe-live` возвращает **только расшифровку**, а не ответ. Его 0.29 с нельзя объявлять скоростью ответа на вопрос. У 2.5 Preview 09-2025 смена вопроса дважды закончилась пустым ходом в разных сериях — быстрый первый ответ не компенсирует этот дефект.

Начальный файл `live_survey.jsonl` прекращал отправку PCM после `audio_stream_end`; 3.8 Live и Extended Thinking тогда не ответили до лимита. После сохранения потока тихих PCM обе модели ответили. Поэтому начальные тайм-ауты не используются как доказательство медленной 3.8: это различие протокола теста. Результат проверен и настоящим кодом приложения (`app_live.jsonl`, затем `option_q.jsonl`).

## Эксперимент: распознавание + текстовая модель

`cascade.jsonl`: отдельно проверена 3.5 Transcribe Live с Gemma 4 26B/31B, 2.5 Flash, 3 Flash Preview, 3.1 Flash-Lite, 3.5 Flash-Lite, 3.6 и 3.8 Flash. Оба Gemma не прошли надёжно (504/500); некоторые новые Flash медленные/503/тайм-аут. Для корректно распознанных арифметики и кеша связка Transcribe Live + 2.5 Flash дала первые слова около 0.66 и 0.72 с. **Это экспериментальная сумма STT и времени отдельного текстового вызова**, а не непрерывная end-to-end интеграция; стоимость повторного запроса/инициализации может отличаться.

В одном опыте `input_transcription` первого вопроса содержала уже формулировку ответа вместо произнесённого вопроса; текст сохранён в raw. Эту пробу нельзя считать корректной расшифровкой или победой конвейера. Требуются более широкие проверки узнавания, границ фразы и очереди вопросов; архитектура приложения на эту связку не переключена.

Первый пилот каскада имел timeout 8 с, который API отклонил: минимальный передаваемый deadline 10 с. Такие строки — ошибка конфигурации теста, не оценка скорости модели. Исправленный прогон использует 10 с; ранние строки сохранены для аудита.

## Что пишут разработчики и Google

1. [Официальный каталог моделей](https://ai.google.dev/gemini-api/docs/models): 3.8 Live рекомендована для низкой задержки голосовых агентов, но рекомендация не гарантирует лучший локальный TTFT. Алиасы могут перенаправляться: например `gemini-flash-latest` в этом тесте вернул версию 3.8 Flash, `gemini-3.5-flash` — 3.6 Flash. Версии сохранены в REST raw.
2. [Live API capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities): 3.8 Live не принимает thinking_level; 3.8 Extended Thinking — low/medium/high; 3.1 — minimal/low/medium/high. Настройки в тестах учтены. На первом вызове flash-latest MINIMAL отвергнут, повторный прогон исправлен на LOW.
3. [Сообщение автора замеров от 6 октября](https://discuss.ai.google.dev/t/gemini-api-latency-has-recently-increased-significantly-especially-with-store-true/187106): автор сообщил рост TTFT и разницу store=true/false в **Interactions API**. Наше приложение использует generateContent и Live, не Interactions; автоматически переносить эту причину на его 15 с нельзя.
4. [Отчёт разработчика о росте задержки длинных Live-сессий](https://discuss.ai.google.dev/t/gemini-live-api-native-audio-response-latency-gradually-increases-during-long-sessions/115508): это наблюдение автора, не доказанная причина здесь. Наши сеансы короткие; деградация через десятки минут пока не измерена.
5. [Сравнение расшифровки на французских медицинских диктовках](https://discuss.ai.google.dev/t/audio-transcription-regression-gemini-3-5-flash-lite-has-3-55x-higher-wer-than-3-1-flash-lite-on-a-french-radiology-test-dataset/180033): автор получил хуже WER у 3.5 Flash-Lite при близкой задержке. Это аргумент отдельно проверять качество, не переносить результаты чужого языка и домена на русский звонок.
6. [Transcribe Live](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe): распознавание речи — отдельная задача от разговорного ответа. Отрицательная/ранняя задержка перевода в `live_survey.jsonl` также не является правильным ответом на исходный вопрос.

## Изменения приложения

- Live ⌥Q: 3.1 minimal сохранена; в локальном контроле быстрее 3.8.
- Отдельный голосовой REST-резерв: **2.5 Flash, thinking_budget=0 → 3 Flash Preview, minimal**. Экранные запросы и вопросы по файлам сохраняют свою цепочку моделей.
- Для голосового REST тайм-аут 10 с; общий клиент KeyPool задаёт 20 с, которые ранее наследовались без отдельного override. Тайм-аут/503 до первых слов допускает переход на резерв. После начала ответа текст сохраняется, переотправки нет. Это предел отдельной попытки ожидания ввода-вывода/серверного deadline, не обещание общего запроса ≤10 с: несколько моделей/ключей и ожидание Live могут суммироваться.
- Звук резервного запроса сохраняется до ожидания Live, чтобы последующие секунды не вытеснили начало вопроса из 8-секундного буфера.

## Ограничения вывода

Один Mac, один API-профиль, один вечер, 3 синтетических коротких вопроса и несколько повторов. Нет теста реального шумного звонка, длинной истории, сложного программирования, регионального переноса или нагрузки по времени суток. Причину «почему именно вечером» эти данные не устанавливают. 15 секунд подтверждены как возможный симптом (14.10 с до текста у alias Flash-Lite и 20 с timeout у прямого 3.5 Flash-Lite), но не как гарантированная характеристика модели. Модель 2.5 Flash — старое поколение; её доступность в будущем требует повторной проверки.

## Все 63 идентификатора

| Идентификатор | Покрытие / результат |
|---|---|
'''
for name,value in inventory.items():
    model=name.removeprefix('models/')
    rows=[r for r in all_rows if r['model']==model and r.get('mode')!='cascade']
    if rows:
        okay=sum(r['status']=='ok' for r in rows)
        errors=collections.Counter()
        for r in rows:
            e=r.get('error','')
            if r['status']=='ok':continue
            if 'limit: 0' in e:reason='quota=0'
            elif '503' in e:reason='503 high demand'
            elif '404' in e:reason='404 unavailable'
            elif 'Audio input modality' in e:reason='нет аудиовхода'
            elif 'Thinking level' in e:reason='исправлена настройка thinking'
            elif 'Developer instruction' in e:reason='transcribe: не принимает system instruction'
            elif 'combination of response modalities' in e:reason='несовместимый тип выхода'
            elif r['status']=='empty':reason='пустой ответ'
            else:reason=r.get('error_type') or r['status']
            errors[reason]+=1
        note=f"API-проб: {len(rows)}, непустых успешных: {okay}"
        if errors:note+='; '+', '.join(f'{k} ×{v}' for k,v in errors.items())
        if 'transcribe' in model:note+='; только распознавание, не прямой ответ'
        if 'translate' in model:note+='; перевод, не ответ'
    elif 'tts' in model:note='Исключена: text-to-speech, не голосовой вопрос → ответ'
    elif 'lyria' in model:note='Исключена: музыка'
    elif 'veo' in model:note='Исключена: генерация видео'
    elif 'embedding' in model:note='Исключена: эмбеддинги, не генерация ответа'
    elif 'nano-banana' in model:note='Исключена: генерация изображений'
    else:note='Исключена: специализированный generateAnswer, без аудио generateContent/Live'
    report+=f'| `{model}` | {note} |\n'
report+='''
## Как повторить

```sh
.venv/bin/python benchmarks/make_audio.py
.venv/bin/python benchmarks/voice_models.py --phase rest --output repeat-rest.jsonl
.venv/bin/python benchmarks/voice_models.py --phase live --repeats 3 --output repeat-live.jsonl
.venv/bin/python benchmarks/option_q_probe.py gemini-3.1-flash-live-preview gemini-3.8-live
.venv/bin/python benchmarks/reserve_probe.py
```

Нужны существующий API-профиль приложения в Keychain, ffmpeg и macOS Milena. Запуски делают реальные API-вызовы и расходуют квоту. Старые raw не очищаются: новый результат добавляется в JSONL. Для строгого повторения запускайте одну серию за раз; в итоговом артефакте сохранены реальные перекрытия и ошибки, а не отобранные лучшие ответы. WAV игнорируются Git; генератор и manifest сохраняют воспроизводимость. Ключи не сохраняются в отчёт или raw.
'''
(ROOT/'REPORT.md').write_text(report)
(ROOT/'environment.json').write_text(json.dumps({'platform':platform.platform(),'python':platform.python_version(),
  'google-genai':importlib.metadata.version('google-genai'),'httpx':importlib.metadata.version('httpx'),
  'profiles_tested':1,'catalog_ids':len(inventory),'rows':len(all_rows)},indent=2))
print('Report generated:',len(all_rows),'raw rows;',len({r['model'] for r in all_rows if r.get('mode')!='cascade'}),'model ids attempted')
